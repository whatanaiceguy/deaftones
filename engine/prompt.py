"""The prompt exactly as the model was trained on it: this system prompt, then the user turn
"Synth: <name>." and the request on the next line, through the model's own chat template."""

SYSTEM_PROMPT = (
    "You are a sound designer. Given a request, and at times a recording of the preset playing, "
    "you write one preset for the named synth in .idc: your reasoning in <think>, then the preset."
)


def messages(synth_name, request):
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Synth: {synth_name}.\n{request.strip()}"}]


def chat_ids(tokenizer, msgs):
    out = tokenizer.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True)
    if hasattr(out, "keys"):
        out = out["input_ids"]
    if hasattr(out, "tolist"):
        out = out.tolist()
    if out and isinstance(out[0], list):
        out = out[0]
    return list(out)
