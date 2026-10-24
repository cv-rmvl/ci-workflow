#!/bin/bash

set -eEuo pipefail

C_RESET='' C_DIM='' C_GREEN='' C_RED='' C_CYAN=''
if [[ -t 1 && ${TERM:-} != dumb && -z ${NO_COLOR+x} ]]; then
  C_RESET=$'\033[0m'
  C_DIM=$'\033[90m'
  C_GREEN=$'\033[32m'
  C_RED=$'\033[31m'
  C_CYAN=$'\033[36m'
fi

sudo_keepalive_pid=''
input_active=0
UI_CLOSED=0
FAIL_SHOWN=0
UI_TTY=0
failure_message='安装失败'
if [[ -t 1 && ${TERM:-} != dumb ]]; then
  UI_TTY=1
fi

ui_line() {
  printf '%b│%b  %s\n' "$C_DIM" "$C_RESET" "$1"
}

ui_info() {
  printf '%b│  %s%b\n' "$C_DIM" "$1" "$C_RESET"
}

ui_blank() { printf '%b│%b\n' "$C_DIM" "$C_RESET"; }

ui_close() {
  if [[ $UI_CLOSED -eq 0 ]]; then
    printf '%b└%b\n' "$C_DIM" "$C_RESET"
    UI_CLOSED=1
  fi
}

ui_fail_footer() {
  if [[ $FAIL_SHOWN -eq 0 && $UI_CLOSED -eq 0 ]]; then
    printf '%b└%b  %b✘ %s%b\n' "$C_DIM" "$C_RESET" "$C_RED" "$1" "$C_RESET"
    UI_CLOSED=1
    FAIL_SHOWN=1
  fi
}

ui_prompt_active() {
  printf '%b◆%b  %s\n' "$C_CYAN" "$C_RESET" "$1"
}

ui_prompt_done() {
  if [[ $UI_TTY -eq 1 ]]; then
    # 与 RDT 相同，回到提示行把实心菱形改为完成状态，再回到当前行。
    printf '\033[2A\r%b◇%b  %s\033[K\033[2B\r' "$C_CYAN" "$C_RESET" "$1"
  else
    printf '%b◇%b  %s\n' "$C_CYAN" "$C_RESET" "$1"
  fi
}

ui_input_frame_start() {
  input_active=1
  printf '%b│%b  ' "$C_DIM" "$C_RESET"
  if [[ $UI_TTY -eq 1 ]]; then
    printf '\n%b└%b\033[1A\r%b│%b  \033[s' "$C_DIM" "$C_RESET" "$C_DIM" "$C_RESET"
  fi
}

ui_input_frame_end() {
  if [[ $UI_TTY -eq 1 ]]; then
    printf '\033[u%b\033[K\033[1B\r%b│%b  \033[K\r' "$C_RESET" "$C_DIM" "$C_RESET"
  else
    printf '\n'
  fi
  input_active=0
}

on_interrupt() {
  local status=$1
  trap '' INT TERM
  trap - ERR
  unset password
  printf '%b\n' "$C_RESET"
  input_active=0
  ui_fail_footer '操作取消'
  exit "$status"
}

on_error() {
  local status=$?
  trap - ERR
  if [[ $input_active -eq 1 ]]; then
    ui_input_frame_end
  fi
  ui_fail_footer "$failure_message"
  exit "$status"
}

ui_header() {
  local title='RMVL KDT Agent installation wizard' i steps r g b
  printf '%b┌%b  ' "$C_DIM" "$C_RESET"
  if [[ -n $C_RESET ]]; then
    steps=$((${#title} - 1))
    for ((i = 0; i < ${#title}; ++i)); do
      r=$((64 + (164 - 64) * i / steps))
      g=$((207 + (92 - 207) * i / steps))
      b=$((144 + (255 - 144) * i / steps))
      printf '\033[38;2;%d;%d;%dm%s' "$r" "$g" "$b" "${title:i:1}"
    done
  else
    printf '%s' "$title"
  fi
  printf '%b\n' "$C_RESET"
}

cleanup() {
  local status=$?
  trap - EXIT ERR
  if [[ $input_active -eq 1 ]]; then
    unset password
    printf '%b\n' "$C_RESET"
    input_active=0
  fi
  if [[ -n $sudo_keepalive_pid ]]; then
    kill "$sudo_keepalive_pid" 2>/dev/null || true
    wait "$sudo_keepalive_pid" 2>/dev/null || true
  fi
  if [[ $status -ne 0 ]]; then
    ui_fail_footer "$failure_message"
  fi
  ui_close
  exit "$status"
}

keep_sudo_alive() {
  trap - ERR
  sleeper=''
  trap 'if [[ -n $sleeper ]]; then kill "$sleeper" 2>/dev/null || true; wait "$sleeper" 2>/dev/null || true; fi' EXIT
  trap 'exit 0' INT TERM
  while true; do
    sleep 30 &
    sleeper=$!
    wait "$sleeper" || exit 0
    sleeper=''
    sudo -n -v || exit 1
  done
}

read_masked_password() {
  local key=''
  password=''
  while IFS= read -r -s -n 1 key; do
    case "$key" in
      '') return 0 ;;
      $'\177'|$'\b')
        if [[ -n $password ]]; then
          password=${password%?}
          printf '\b \b'
        fi
        ;;
      $'\025')
        while [[ -n $password ]]; do
          password=${password%?}
          printf '\b \b'
        done
        ;;
      $'\004') return 1 ;;
      *) password+="$key"; printf '*' ;;
    esac
  done
  return 1
}

authenticate_sudo() {
  failure_message='sudo 验证失败'
  if sudo -n -v >/dev/null 2>&1; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    failure_message='没有可用的交互输入，请在终端中运行，或先执行 sudo -v。'
    return 1
  fi

  local password='' status=0
  local prompt='请输入本机密码以继续安装'
  ui_prompt_active "$prompt"
  ui_input_frame_start
  if ! read_masked_password; then
    unset password
    ui_input_frame_end
    failure_message='密码输入已取消'
    return 1
  fi
  ui_input_frame_end
  ui_prompt_done "$prompt"
  if [[ -z $password ]]; then
    unset password
    failure_message='未输入密码，无法继续安装'
    return 1
  fi
  # 不把密码放进参数或环境变量，验证后立即清除，后续仅使用 sudo 的授权缓存。
  printf '%s\n' "$password" | sudo -S -p '' -v >/dev/null 2>&1 || status=$?
  unset password
  if [[ $status -ne 0 ]]; then
    failure_message='sudo 验证失败，请检查密码或当前用户的 sudo 权限'
    return "$status"
  fi
}

run_step() {
  local title=$1 status line
  shift
  failure_message="${title}失败"
  ui_info "正在${title}"
  if "$@" 2>&1 | while IFS= read -r line || [[ -n $line ]]; do ui_line "$line"; done; then
    printf '%b│%b  %b✔ %s完成%b\n' "$C_DIM" "$C_RESET" "$C_GREEN" "$title" "$C_RESET"
  else
    status=$?
    return "$status"
  fi
}

main() {
  if [[ $# -gt 0 ]]; then
    if [[ $# -eq 1 && ( $1 == --help || $1 == -h ) ]]; then
      printf '用法：bash %s\n配置、构建并安装 KDT Agent，构建前通过 sudo 获取授权。\n' "${BASH_SOURCE[0]}"
      return 0
    fi
    printf '此脚本不接受参数，使用 --help 查看说明。\n' >&2
    return 2
  fi

  ui_header
  trap cleanup EXIT
  trap 'on_interrupt 130' INT
  trap 'on_interrupt 143' TERM
  trap on_error ERR

  local root command
  root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  cd -- "$root"
  for command in cmake sudo; do
    if ! command -v "$command" >/dev/null 2>&1; then
      failure_message="缺少必要命令：$command，请先安装"
      return 127
    fi
  done
  if [[ ! -f kdt/agents/CMakeLists.txt ]]; then
    failure_message='未找到 kdt/agents/CMakeLists.txt，请在完整的 ci-workflow 仓库中运行脚本'
    return 1
  fi

  ui_info "工作目录：$root"
  ui_blank
  authenticate_sudo
  ui_blank
  keep_sudo_alive &
  sudo_keepalive_pid=$!

  run_step '配置 KDT Agent ' cmake -S kdt/agents -B build/agents
  run_step '构建 KDT Agent ' cmake --build build/agents --parallel
  run_step '安装 KDT Agent ' sudo -n cmake --install build/agents
  printf '%b│%b  %b✔ 安装完成%b\n' "$C_DIM" "$C_RESET" "$C_GREEN" "$C_RESET"
}

main "$@"

