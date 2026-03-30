#!/usr/bin/env python
"""
端到端测试一键运行脚本

提供彩色输出、测试汇总和详细错误信息
"""

import sys
import os
import argparse
import subprocess
import time
from typing import List, Tuple

# 添加项目根目录到 Python 路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SCRIPT_DIR)))
sys.path.insert(0, PROJECT_ROOT)


# ANSI 颜色代码
class Colors:
    """终端颜色常量"""

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_header(title: str):
    """打印标题头"""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}  {title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'=' * 60}{Colors.RESET}\n")


def print_success(message: str):
    """打印成功信息"""
    print(f"{Colors.GREEN}✓ {message}{Colors.RESET}")


def print_error(message: str):
    """打印错误信息"""
    print(f"{Colors.RED}✗ {message}{Colors.RESET}")


def print_warning(message: str):
    """打印警告信息"""
    print(f"{Colors.YELLOW}! {message}{Colors.RESET}")


def print_info(message: str):
    """打印信息"""
    print(f"{Colors.BLUE}ℹ {message}{Colors.RESET}")


def check_service() -> bool:
    """检查 TrajProxy 服务是否可用"""
    import requests
    from tests.e2e.config import PROXY_URL

    try:
        response = requests.get(f"{PROXY_URL}/health", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def run_tests(
    test_modules: List[str] = None,
    verbose: bool = False,
    markers: str = None,
    extra_args: List[str] = None
) -> Tuple[int, str]:
    """
    运行 pytest 测试

    参数:
        test_modules: 要测试的模块列表，为空则测试全部
        verbose: 是否显示详细输出
        markers: pytest 标记表达式
        extra_args: 额外的 pytest 参数

    返回:
        (返回码, 输出内容)
    """
    # 构建 pytest 命令
    pytest_args = [
        sys.executable, "-m", "pytest",
        "-v" if verbose else "-q",
        "--tb=short",
        "-W", "ignore::DeprecationWarning",
    ]

    # 添加颜色输出
    pytest_args.append("--color=yes")

    # 添加标记过滤
    if markers:
        pytest_args.extend(["-m", markers])

    # 添加测试模块
    if test_modules:
        test_dir = os.path.dirname(__file__)
        for module in test_modules:
            test_file = os.path.join(test_dir, f"test_{module}.py")
            if os.path.exists(test_file):
                pytest_args.append(test_file)
            else:
                print_warning(f"测试模块不存在: test_{module}.py")
    else:
        # 测试当前目录下所有测试文件
        pytest_args.append(os.path.dirname(__file__))

    # 添加额外参数
    if extra_args:
        pytest_args.extend(extra_args)

    # 运行测试
    result = subprocess.run(pytest_args, capture_output=True, text=True)

    return result.returncode, result.stdout + result.stderr


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="TrajProxy 端到端测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 运行所有测试
  python run_e2e.py

  # 运行特定模块测试
  python run_e2e.py --module health chat

  # 只运行集成测试
  python run_e2e.py --marker integration

  # 详细输出
  python run_e2e.py -v

  # 跳过慢速测试
  python run_e2e.py --marker "not slow"
        """
    )

    parser.add_argument(
        "-m", "--module",
        nargs="+",
        choices=["health", "chat", "models", "trajectory"],
        help="指定要测试的模块"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细输出"
    )
    parser.add_argument(
        "--marker",
        type=str,
        help="pytest 标记表达式，如 'integration' 或 'not slow'"
    )
    parser.add_argument(
        "--skip-service-check",
        action="store_true",
        help="跳过服务检查"
    )
    parser.add_argument(
        "extra_args",
        nargs="*",
        help="传递给 pytest 的额外参数"
    )

    args = parser.parse_args()

    # 打印标题
    print_header("TrajProxy 端到端测试")

    # 检查服务
    if not args.skip_service_check:
        print_info("检查服务状态...")
        if check_service():
            print_success("TrajProxy 服务正常运行")
        else:
            print_error("TrajProxy 服务不可用")
            print_warning("请确保 TrajProxy 已启动（如: cd traj_proxy && ./start_docker.sh）")
            print_warning("或使用 --skip-service-check 跳过检查")
            return 1

    # 打印测试配置
    print_info(f"测试模块: {args.module or '全部'}")
    print_info(f"标记过滤: {args.marker or '无'}")
    print()

    # 运行测试
    start_time = time.time()
    return_code, output = run_tests(
        test_modules=args.module,
        verbose=args.verbose,
        markers=args.marker,
        extra_args=args.extra_args
    )
    elapsed = time.time() - start_time

    # 输出结果
    print(output)

    # 打印汇总
    print_header("测试汇总")
    print_info(f"总耗时: {elapsed:.2f} 秒")

    if return_code == 0:
        print_success("所有测试通过！")
        return 0
    else:
        print_error("部分测试失败")
        return return_code


if __name__ == "__main__":
    sys.exit(main())
