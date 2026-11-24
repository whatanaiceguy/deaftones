"""Fast text generation for the trained model on one GPU: the prompt goes through the model's own
forward (audio included), then every token through a Qwen3 decode step of our own over a fixed KV
cache, recorded as a HIP/CUDA graph per context bucket and replayed.

    fg = FastGen(model, tok)
    for piece, n in fg.stream(ids, max_new=4800, temperature=0.7, top_k=45): ...
"""
import torch
import bitsandbytes as bnb
import bitsandbytes.functional as BF
from transformers import DynamicCache
from transformers.models.qwen3.modeling_qwen3 import apply_rotary_pos_emb


def unnest_4bit(model):
    n = 0
    for mod in model.modules():
        if isinstance(mod, bnb.nn.Linear4bit):
            st = mod.weight.quant_state
            if st is not None and st.nested:
                st.absmax = BF.dequantize_blockwise(st.absmax, st.state2) + st.offset
                st.nested, st.state2, st.offset = False, None, None
                n += 1
    return n


class FastGen:
    def __init__(self, model, tok, max_ctx=8192, bucket=256):
        self.model, self.tok = model, tok
        self.lm = model.language_model
        cfg = self.lm.config
        self.heads, self.kv, self.hd = cfg.num_attention_heads, cfg.num_key_value_heads, cfg.head_dim
        self.scale = self.hd ** -0.5
        self.max_ctx, self.bucket = max_ctx, bucket
        dev, dt = self.lm.embed_tokens.weight.device, self.lm.embed_tokens.weight.dtype
        self.dev, self.dt = dev, dt
        shape = (1, self.kv, max_ctx, self.hd)
        self.kc = [torch.zeros(shape, device=dev, dtype=dt) for _ in self.lm.layers]
        self.vc = [torch.zeros(shape, device=dev, dtype=dt) for _ in self.lm.layers]
        self.tok_t = torch.zeros(1, dtype=torch.long, device=dev)
        self.pos_t = torch.zeros(1, dtype=torch.long, device=dev)
        self.ar = torch.arange(max_ctx, device=dev)
        self.graphs, self.outs = {}, {}
        self.pool = torch.cuda.graph_pool_handle()
        self.eos = {tok.eos_token_id} | set(getattr(model.generation_config, "eos_token_id", None) or [])

    def step(self, L):
        lm = self.lm
        h = lm.embed_tokens(self.tok_t.view(1, 1))
        cos, sin = lm.rotary_emb(h, self.pos_t.view(1, 1))
        neg = torch.where(self.ar[:L] <= self.pos_t, 0.0, float("-inf")).to(torch.float32)
        for i, layer in enumerate(lm.layers):
            a = layer.self_attn
            x = layer.input_layernorm(h)
            q = a.q_norm(a.q_proj(x).view(1, 1, self.heads, self.hd)).transpose(1, 2)
            k = a.k_norm(a.k_proj(x).view(1, 1, self.kv, self.hd)).transpose(1, 2)
            v = a.v_proj(x).view(1, 1, self.kv, self.hd).transpose(1, 2)
            q, k = apply_rotary_pos_emb(q, k, cos, sin)
            self.kc[i].index_copy_(2, self.pos_t, k)
            self.vc[i].index_copy_(2, self.pos_t, v)
            K, V = self.kc[i][:, :, :L], self.vc[i][:, :, :L]
            qg = q.reshape(1, self.kv, self.heads // self.kv, self.hd)
            s = torch.matmul(qg, K.transpose(-1, -2)).float() * self.scale + neg
            p = torch.softmax(s, dim=-1).to(self.dt)
            o = torch.matmul(p, V).reshape(1, 1, self.heads * self.hd)
            h = h + a.o_proj(o)
            x = layer.post_attention_layernorm(h)
            h = h + layer.mlp(x)
        return self.model.lm_head(lm.norm(h))[:, -1].float()

    def decode(self, L):
        if L not in self.graphs:
            s = torch.cuda.Stream()
            s.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(s):
                out = self.step(L)
            torch.cuda.current_stream().wait_stream(s)
            g = torch.cuda.CUDAGraph()
            with torch.cuda.graph(g, pool=self.pool):
                self.outs[L] = self.step(L)
            self.graphs[L] = g
            return out
        self.graphs[L].replay()
        return self.outs[L]

    def prefill(self, ids, **audio):
        x = torch.tensor([ids], device=self.dev)
        cache = DynamicCache()
        out = self.model(input_ids=x, attention_mask=torch.ones_like(x), past_key_values=cache, use_cache=True, **audio)
        P = x.shape[1]
        for i, layer in enumerate(cache.layers):
            self.kc[i][:, :, :P].copy_(layer.keys)
            self.vc[i][:, :, :P].copy_(layer.values)
        return out.logits[:, -1].float()

    @staticmethod
    def pick(logits, temperature, top_k):
        if not temperature:
            return logits.argmax(-1)
        logits = logits / temperature
        if top_k:
            v, ix = torch.topk(logits, top_k, dim=-1)
            return ix.gather(-1, torch.multinomial(torch.softmax(v, -1), 1)).view(-1)
        return torch.multinomial(torch.softmax(logits, -1), 1).view(-1)

    @torch.no_grad()
    def stream(self, ids, max_new=4800, temperature=0.7, top_k=45, stop=lambda: False, **audio):
        P = len(ids)
        max_new = min(max_new, self.max_ctx - P)
        logits = self.prefill(ids, **audio)
        out, a, b = [], 0, 0
        self.ids, self.ended = out, False
        for n in range(max_new):
            t = self.pick(logits, temperature, top_k)
            self.tok_t.copy_(t)
            tid = int(t.item())
            if tid in self.eos:
                self.ended = True
                break
            out.append(tid)
            prev = self.tok.decode(out[a:b], skip_special_tokens=True)
            cur = self.tok.decode(out[a:], skip_special_tokens=True)
            if not cur.endswith("�") and len(cur) > len(prev):
                yield cur[len(prev):], len(out)
                a, b = b, len(out)
            if stop() or n == max_new - 1:
                break
            pos = P + len(out) - 1
            self.pos_t.fill_(pos)
            L = min(self.max_ctx, (pos // self.bucket + 1) * self.bucket)
            logits = self.decode(L)
