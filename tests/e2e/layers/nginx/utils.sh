#!/bin/bash
# Layer 1 utils 包装: 加载共享 utils + nginx 层配置

_LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_LAYER_DIR}/../../utils.sh"
source "${_LAYER_DIR}/config.sh"
