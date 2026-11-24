# deaftones

**Describe a sound, pick a synth, get a playable preset.**

deaftones is an 8B language model that writes synth patches for **Serum 2**, **Vital** and **Diva**.
You type what you want to hear. It reasons about the sound, then writes the preset as `.idc`: plain
text, one line per module, values in ms, Hz and dB, with comments on why each part is set that way.
This repo turns the `.idc` into the synth's own preset file and plays it through the synth, so you
hear the result right away and can load it in your DAW.

```
Synth: Vital.   Glassy bell pluck
```

```
<think>
A high, glassy bell pluck with a quick strike, a brief sinking body and a little room to ring after
each note. Its pitch stays steady, while the upper partials give it a bright, chiming character ...

I build the core from a single synthesized pulse-like source, then use the second oscillator as a
frequency modulator to add a sharper, more complex edge to that carrier ...
</think>

@synth vital
@preset Glassy Bell Pluck
@macro1 PUCK

// Using a synthesized pulse-like source as the carrier gives the pluck its clear, bright harmonic
// basis; formant scaling reshapes its partials for a glassier chime.
osc1 on {level:-4.28dB transpose:0 distortion_type:fm_osc2 distortion_amount:0.16
         spectral_morph_type:formant_scale spectral_morph_amount:0.69 ...}
env1 {attack:7.78ms decay:243.16ms sustain:0.15 release:90.18ms ...}
chorus on {voices:4 frequency:0.125Hz dry_wet:0.05 ...}
reverb on {decay_time:1s size:0.5 dry_wet:0.06 ...}
...
```

Everything after `</think>` is the `.idc`. The converter writes it out as `Glassy Bell Pluck.vital`,
and the renderer plays that through Vital. On a mid-range GPU you hear the preset about 40 seconds
after asking for it.

## Models

All on Hugging Face:

| | size | for |
|---|---:|---|
| [deaftones-8B-GGUF](https://huggingface.co/IDONTCARELETMEIN/deaftones-8B-GGUF) `Q4_K_M` | 5.0 GB | **the default here**, llama.cpp |
| [deaftones-8B-GGUF](https://huggingface.co/IDONTCARELETMEIN/deaftones-8B-GGUF) `Q8_0` | 8.7 GB | llama.cpp, closest to the full model |
| [deaftones-8B-bnb-nf4](https://huggingface.co/IDONTCARELETMEIN/deaftones-8B-bnb-nf4) | 7.3 GB | transformers (`--backend hf`) |
| [deaftones-8B](https://huggingface.co/IDONTCARELETMEIN/deaftones-8B) | 18.1 GB | the full bf16 model, and the model card |

## What you need

- **Windows.** The renderer and the llama.cpp handling are written for it.
- **Python 3.10 or later.**
- **A GPU for llama.cpp:**
  - Any vendor works, through llama.cpp's Vulkan build.
  - The default Q4_K_M takes about 5.5 GB of GPU memory at the full 8,192-token context.
  - With 10 GB or more, `--quant Q8_0` gives output closer to the full model, a bit slower.
  - It also runs on the CPU, slowly.
- **The synths you want presets for,** installed as VST3: Serum 2, Vital, Diva. Without a synth's
  plugin you still get its preset file, just no audio preview.
- **About 6.5 GB of disk** for the model, llama.cpp and the content pack.

## Setup

```
git clone https://github.com/whatanaiceguy/deaftones
cd deaftones
pip install -r requirements.txt
python configure.py
```

`configure.py` asks one thing at a time, with a default in brackets: Enter takes it, a path replaces
it, `-` skips that part.

1. **Python packages:** checks them and offers to install what is missing.
2. **The model:** downloads `deaftones-8B-Q4_K_M.gguf`, or uses one you already have.
   `--quant Q8_0` takes the larger one.
3. **llama.cpp:** uses your `llama-server.exe`, or downloads the latest Windows Vulkan build.
4. **Plugins:** finds the VST3s in `C:\Program Files\Common Files\VST3`, or asks where they are.
5. **Factory content:**
   - downloads the content pack, 240 MB: the wavetables, samples and LFO shapes the presets refer
     to (see below);
   - reads your Serum 2 install (`Documents\Xfer\Serum 2 Presets`) for its table paths.

Everything goes to `settings.json`. Run it again to change anything. `python configure.py --yes`
takes every default without asking.

## Use

```
python serve.py
```

Open http://127.0.0.1:8774.

1. Pick a synth and describe the sound.
2. The model's reasoning streams in, then the preset text, with a live token count and speed.
3. When it is done, the preset is converted, rendered and played.
4. **Show in folder** opens the preset file in Explorer; drag it into the synth or your DAW.

The library on the left lists every earlier preset, each with its own play button. Deleting one
moves its folder to the Recycle Bin. Each answer is saved in `out/<time>_<synth>/`: the request,
the model's text, the preset file, and a `.wav` of the test pattern (single notes, legato, staccato
and a chord, 10.5 s).

Sampling defaults to temperature 0.9 and top-k 45; both can be changed on the page, under
**Sampling**. Ctrl+C stops the server, and llama-server with it.

To convert an answer saved as text without the page:

```
python convert.py answer.txt [serum2|vital|diva]
```

### Speed

Measured on an AMD RX 7600 XT (16 GB):

| backend | tokens/s | GPU memory | a typical answer (1,300-2,000 tokens) |
|---|---:|---:|---:|
| llama.cpp Vulkan, Q4_K_M (default) | 45-47 | 5.5 GB | 30-45 s |
| transformers, nf4 (`--backend hf`) | 27 | 8.7 GB | 50-75 s |

## How it works

- **The model:** a fine-tune of [MOSS-Music-8B](https://huggingface.co/OpenMOSS-Team/MOSS-Music-8B-Instruct),
  a Qwen3-8B language model with an audio encoder. It was trained on about 9,000 Serum 2, Vital
  and Diva presets. Each preset came with a request describing it and reasoning about how its
  settings make that sound, and most of the time with a recording of it playing as well. Asked for
  a sound in words alone, it writes the reasoning and then the settings. Details
  are on the [model card](https://huggingface.co/IDONTCARELETMEIN/deaftones-8B).
- **The preset as text (`.idc`):** one line per module (oscillators, envelopes, filters, effects,
  modulation routes), values in units an ear maps to (ms, Hz, dB). Each line has a comment saying
  why it is set that way.
- **The converters** (`converters/`) turn that text back into the synth's own file: `.SerumPreset`,
  `.vital` or `.h2p`. They read loosely, so a key the synth does not have, or a route it cannot
  take, is skipped and listed on the page rather than failing the preset.
- **The renderers** (`render/`) load the preset into the real plugin through DawDreamer and play the
  test pattern.

### Wavetables, samples and LFO shapes

The model refers to these by names it learned in training:

- **Serum 2's factory tables, noises and impulses** are referenced by path. Serum loads them from
  your own install.
- **Vital's factory content, and the factory content Serum 2 presets embed,** come from the
  [content pack](https://huggingface.co/datasets/IDONTCARELETMEIN/deaftones-content).
  - Each item is stored under the name the model learned, with a hash of its content
    (`converters/*/factory.json`).
- **Anything else the model names** falls back to the nearest factory one. That covers a
  non-factory table, or a name it made up. The preset still loads and plays, but that oscillator
  may sound different from what the model meant.

## Limitations

- **Text to text.** This repo takes a description in words. The full model
  ([deaftones-8B](https://huggingface.co/IDONTCARELETMEIN/deaftones-8B) and its bnb versions) also
  takes a recording with the request; this repo does not use that.
- **Windows only** for now.
- **A preset can come out quiet or near-silent.** In a 50-request test, 5 rendered below −30 dBFS.
  The `.wav` keeps the preset's own level (it is only scaled down when it would clip), and the page
  shows its peak.
- **Serum 2 renders run hot** (often above 0 dBFS); the `.wav` is scaled down, the preset is not.
- **Macro and modulation routes a synth cannot take are skipped,** most often on Diva. The page
  lists them.

## Troubleshooting

- **Slow generation:** llama-server may be running on the CPU. `out/llama-server.log` names the
  device it found. The Vulkan build that `configure.py` downloads works on any GPU vendor.
- **No sound for one synth:** its plugin path is not set. Run `python configure.py` again, or check
  `settings.json`.

## Running the transformers backend

```
pip install -r requirements-hf.txt
python configure.py --hf
python serve.py --backend hf
```

This fetches deaftones-8B-bnb-nf4 and runs it in transformers. It needs torch with GPU support and
bitsandbytes 0.50.2 or later; 0.50.2 is the first with a Windows ROCm build.

The prompt goes through the model's own forward pass, then `engine/fastgen.py` decodes with a fixed
KV cache and HIP/CUDA graphs.

## License

MIT (`LICENSE`). `engine/moss_music/` is MOSS-Music's model code under Apache-2.0, with its own
`LICENSE` and a `NOTICE` of what was changed. The model weights on Hugging Face are Apache-2.0.

## Notice

Serum is a trademark of Xfer Records, Vital of Vital Audio and Diva of u-he. This project is not
affiliated with, endorsed by or connected to any of them.
