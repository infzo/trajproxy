#!/bin/bash
# Layer 2 utils 包装: 加载共享 utils + proxy 层配置

_LAYER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_LAYER_DIR}/../../utils.sh"
source "${_LAYER_DIR}/config.sh"
