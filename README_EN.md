# ncm2music — NCM Music Format Converter (CLI Batch Version)

Convert NetEase Cloud Music's encrypted NCM files back to normal **MP3 / FLAC**.

- **Zero dependencies**: Runs on Python 3.7+ with no third-party libraries
- **Fully local**: Decryption happens on your machine — no network, no uploads
- **Auto format detection**: Sniffs whether the audio inside is MP3 or FLAC and names the output accordingly
- **Full metadata**: Automatically embeds title / artist / album / cover art (ID3v2.3 for MP3, Vorbis Comment + PICTURE for FLAC)
- **Smart naming**: Output files are named `Artist - Title.flac`; falls back to the original filename when metadata is unavailable

> 中文版文档见 [README.md](README.md)。

## Quick Start

```bash
# Convert a single file (output goes next to the source)
python ncm2music.py "D:\Music\song.ncm"

# Convert every NCM file in a folder
python ncm2music.py "D:\Netease\CloudMusic\Cache"

# Recurse into subfolders + specify output directory (recommended)
python ncm2music.py -r -o "D:\Converted" "D:\Netease\CloudMusic"

# Delete source NCM files after successful conversion (frees disk space — use with care)
python ncm2music.py -m -o "D:\Converted" "D:\Netease\CloudMusic"
```

> On Windows, run from PowerShell or CMD. Quote paths that contain spaces.

## Options

| Option | Description |
|--------|-------------|
| `targets` (required) | NCM files and/or folders containing them; multiple space-separated paths accepted |
| `-o, --out <dir>` | Output directory (defaults to the source file's folder; created automatically) |
| `-r, --recursive` | Process subdirectories recursively (directory mode only) |
| `-m, --delete-src` | Delete the source NCM file after successful conversion |
| `--no-tags` | Skip embedding metadata and cover art; output audio only |

## Output Rules

- **Format**: Detected from the audio content itself (`ID3`/frame sync → MP3, `fLaC` → FLAC, `OggS` → OGG); falls back to the format declared in the file's embedded metadata if sniffing fails
- **Filename**: `Artist - Title` when metadata is available, otherwise the original NCM filename; illegal characters are replaced with `_`
- **Overwrites**: Files with the same name in the output directory are overwritten silently — don't convert twice into the same folder if you want to keep earlier outputs

## FAQ

**Q: "Not a valid NCM file"?**
The file isn't in NetEase's NCM format, or is corrupted. Note that some NetEase Cloud Music 3.0+ clients download plain MP3s — those don't need this tool at all; just rename them.

**Q: The cover art is missing?**
Some NCM files downloaded from NetEase Cloud Music 3.0+ no longer embed cover images (the audio still converts fine — there's just no image to write). You can add covers afterwards with a tagging tool such as MusicBrainz Picard.

**Q: How fast is it?**
Uses a 256-byte periodic keystream with bulk big-integer XOR: a 216 MB Hi-Res file converts in about 2 seconds; a typical 10 MB song is nearly instant.

**Q: Is it safe?**
On startup the built-in AES engine is verified against the official FIPS-197 test vector. If the self-test fails, the program exits immediately rather than producing corrupt output.

## Companion Tool

`NCM音乐格式转换器.html` in the same folder is the web version: double-click to open it in a browser, drag and drop files, same fully-local processing. Ideal for converting a few songs without touching the command line.

## Disclaimer

For personal format conversion and backup of music you have already downloaded only. Please respect copyright — do not distribute the converted files or use them commercially.
