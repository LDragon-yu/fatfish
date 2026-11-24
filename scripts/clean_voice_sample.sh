#!/usr/bin/env bash
# 清理音色克隆参考音频：高通/低通 + FFT 降噪 + 响度归一化。
#
# 用法：
#   ./scripts/clean_voice_sample.sh <输入音频> [输出wav] [nr] [nf]
#
# 默认：
#   nr=12（降噪强度，越大越干净但可能失真）
#   nf=-33（估计的底噪 dB；本机 simple.mp3 约 -33）
#   输出=<输入名>_clean.wav
#
# 例：
#   ./scripts/clean_voice_sample.sh private/voice/simple.mp3 private/voice/simple_clean.wav
#   ./scripts/clean_voice_sample.sh private/voice/simple.mp3 private/voice/simple_clean_strong.wav 18

set -euo pipefail

usage() {
  echo "用法: $0 <输入音频> [输出wav] [nr] [nf]"
  echo "默认: nr=12, nf=-33, 输出=<输入名>_clean.wav"
}

INPUT="${1:-}"
if [[ -z "$INPUT" ]]; then
  usage
  exit 1
fi

OUTPUT="${2:-${INPUT%.*}_clean.wav}"
NR="${3:-12}"
NF="${4:--33}"

ffmpeg -hide_banner -y -v error -i "$INPUT" \
  -af "highpass=f=70,lowpass=f=12000,afftdn=nf=${NF}:nr=${NR},loudnorm=I=-18:TP=-2:LRA=7" \
  -ac 1 -ar 24000 -c:a pcm_s16le "$OUTPUT"

echo "已生成: $OUTPUT"
