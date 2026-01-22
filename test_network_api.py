#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网络和 API 调用诊断工具
专门用于排查网络连接和 API 调用问题
"""

import os
import sys
import time
import socket
import warnings
from pathlib import Path

# 屏蔽干扰警告
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
import requests

def _safe_load_dotenv() -> None:
    """在受限环境（如沙盒）中安全加载 .env：失败则忽略，继续使用现有环境变量。"""
    try:
        load_dotenv()
    except Exception as e:
        # 不要让 .env 读取失败阻塞网络/接口诊断
        msg = str(e)
        if len(msg) > 200:
            msg = msg[:200] + "...（错误信息过长已省略）"
        print(f"⚠️  读取 .env 失败，将继续使用当前环境变量：{msg}")

def test_dns_resolution(host: str, port: int = 443):
    """测试 DNS 解析"""
    print(f"\n[1/6] 测试 DNS 解析: {host}")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        print(f"✅ DNS 解析成功: {host}")
        print(f"   解析到的 IP: {', '.join(ips[:5])}")
        if len(ips) > 5:
            print(f"   ... 还有 {len(ips) - 5} 个 IP")
        return True, ips
    except Exception as e:
        print(f"❌ DNS 解析失败: {e}")
        return False, []


def test_tcp_connection(host: str, port: int = 443, ips: list = None):
    """测试 TCP 连接"""
    print(f"\n[2/6] 测试 TCP 连接: {host}:{port}")
    
    if not ips:
        print("⚠️  无 IP 地址，跳过 TCP 连接测试")
        return False
    
    timeout = 5.0
    connected = False
    
    for ip in ips[:3]:  # 只测试前3个IP
        try:
            print(f"   尝试连接: {ip}:{port}...")
            s = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, port))
            s.close()
            print(f"   ✅ 连接成功: {ip}:{port}")
            connected = True
            break
        except Exception as e:
            print(f"   ❌ 连接失败: {ip}:{port} - {str(e)[:100]}")
            continue
    
    if connected:
        print(f"✅ TCP 连接测试通过")
        return True
    else:
        print(f"❌ TCP 连接测试失败（可能需要代理）")
        return False


def test_proxy_config():
    """检查代理配置"""
    print(f"\n[3/6] 检查代理配置")
    
    proxy_vars = {
        "HTTPS_PROXY": os.getenv("HTTPS_PROXY"),
        "https_proxy": os.getenv("https_proxy"),
        "HTTP_PROXY": os.getenv("HTTP_PROXY"),
        "http_proxy": os.getenv("http_proxy"),
    }
    
    found_proxy = False
    for name, value in proxy_vars.items():
        if value:
            print(f"✅ 检测到代理: {name}={value}")
            found_proxy = True
    
    if not found_proxy:
        print("⚠️  未检测到代理环境变量")
        print("   💡 如果在受限网络环境，可能需要设置 HTTPS_PROXY")
    
    return found_proxy


def test_gemini_api_key():
    """检查 Gemini API Key"""
    print(f"\n[4/6] 检查 Gemini API Key")
    
    _safe_load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    
    if not api_key:
        print("❌ GEMINI_API_KEY 未设置")
        return False
    
    print(f"✅ GEMINI_API_KEY 已设置（长度: {len(api_key)}）")
    print(f"   Key 前缀: {api_key[:10]}...")
    return True


def test_gemini_api_call(api_key: str):
    """测试 Gemini API 调用"""
    print(f"\n[5/6] 测试 Gemini API 调用")
    
    try:
        import google.generativeai as genai
        
        print("   正在配置 Gemini API...")
        # 强制使用 REST 协议，规避 gRPC 连接失败导致的长时间阻塞/重试
        genai.configure(api_key=api_key, transport='rest')
        
        print("   正在创建模型实例...")
        # 使用实际可用的模型（根据 config/analyst_rules.yaml，gemini-2.0-flash-exp 可用）
        # 如果不可用，尝试 gemini-1.5-pro-latest
        try:
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            print("   使用模型: gemini-2.0-flash-exp")
        except Exception:
            print("   ⚠️  gemini-2.0-flash-exp 不可用，尝试 gemini-1.5-pro-latest")
            model = genai.GenerativeModel("gemini-1.5-pro-latest")
            print("   使用模型: gemini-1.5-pro-latest")
        
        print("   正在发送测试请求（最多等待30秒）...")
        t0 = time.time()
        
        # 设置超时保护
        import signal
        def timeout_handler(signum, frame):
            raise TimeoutError("Gemini API 调用超时（30秒）")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(30)
        
        try:
            response = model.generate_content(
                "测试：请回复 'OK'",
                request_options={"timeout": 30}
            )
            signal.alarm(0)
            
            dt = time.time() - t0
            text = response.text if hasattr(response, 'text') else str(response)
            
            print(f"   ✅ API 调用成功（耗时: {dt:.2f} 秒）")
            print(f"   响应预览: {str(text)[:50]}...")
            return True
            
        except TimeoutError:
            signal.alarm(0)
            print("   ❌ API 调用超时（30秒）")
            return False
        except Exception as e:
            signal.alarm(0)
            error_msg = str(e)
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "...（错误信息过长已省略）"
            print(f"   ❌ API 调用失败: {error_msg}")
            return False
            
    except ImportError:
        print("   ❌ google.generativeai 未安装")
        return False
    except Exception as e:
        print(f"   ❌ 测试失败: {e}")
        return False


def test_imagen_api_endpoint():
    """测试 Imagen API 端点（不实际调用）"""
    print(f"\n[6/6] 检查 Imagen API 端点")
    
    _safe_load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    
    if not api_key:
        print("⚠️  GEMINI_API_KEY 未设置，跳过")
        return False
    
    url = "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict"
    
    print(f"   端点: {url}")
    print(f"   注意：Imagen API 可能需要特殊权限，可能返回 403 或 404")
    
    # 检查代理配置
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    proxies = None
    if proxy:
        proxies = {
            "http": proxy,
            "https": proxy
        }
        print(f"   使用代理: {proxy}")
    
    try:
        # 只测试连接，不发送实际请求
        response = requests.head(
            url,
            params={"key": api_key},
            timeout=10,
            allow_redirects=False,
            proxies=proxies  # 使用代理
        )
        
        print(f"   HTTP 状态码: {response.status_code}")
        
        if response.status_code == 200:
            print("   ✅ 端点可访问")
            return True
        elif response.status_code == 403:
            print("   ⚠️  端点返回 403（可能需要额外权限或 API Key 权限不足）")
            return False
        elif response.status_code == 404:
            print("   ⚠️  端点返回 404（这是正常的，Imagen API 可能需要不同的端点或权限）")
            print("   💡 提示：Imagen API 可能不在当前 API Key 的权限范围内")
            return False
        else:
            print(f"   ⚠️  端点返回 {response.status_code}")
            return False
            
    except requests.exceptions.Timeout:
        print("   ❌ 连接超时（10秒）")
        print("   💡 提示：如果使用代理，请确保代理正常工作")
        return False
    except requests.exceptions.ConnectionError:
        print("   ❌ 连接失败（网络问题或代理配置）")
        print("   💡 提示：请检查代理设置或网络连接")
        return False
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "...（错误信息过长已省略）"
        print(f"   ❌ 测试失败: {error_msg}")
        return False


def main():
    """主函数"""
    print("=" * 80)
    print("🔍 网络和 API 调用诊断工具")
    print("=" * 80)
    
    results = {}
    
    # 1. DNS 解析测试
    success, ips = test_dns_resolution("generativelanguage.googleapis.com")
    results["dns"] = success
    
    # 2. TCP 连接测试
    results["tcp"] = test_tcp_connection("generativelanguage.googleapis.com", 443, ips)
    
    # 3. 代理配置检查
    results["proxy"] = test_proxy_config()
    
    # 4. API Key 检查
    results["api_key"] = test_gemini_api_key()
    
    # 5. Gemini API 调用测试
    if results["api_key"]:
        _safe_load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        results["gemini_api"] = test_gemini_api_call(api_key)
    else:
        results["gemini_api"] = None
        print("\n[5/6] 测试 Gemini API 调用 - 跳过（API Key 未设置）")
    
    # 6. Imagen API 端点测试
    if results["api_key"]:
        results["imagen_api"] = test_imagen_api_endpoint()
    else:
        results["imagen_api"] = None
        print("\n[6/6] 检查 Imagen API 端点 - 跳过（API Key 未设置）")
    
    # 总结
    print("\n" + "=" * 80)
    print("📊 诊断总结")
    print("=" * 80)
    
    for name, result in results.items():
        if result is True:
            status = "✅ 通过"
        elif result is False:
            status = "❌ 失败"
        else:
            status = "⏭️  跳过"
        print(f"  {name}: {status}")
    
    print("\n" + "-" * 80)
    
    if not results.get("dns"):
        print("⚠️  问题：DNS 解析失败")
        print("💡 建议：检查网络连接或 DNS 设置（可尝试使用 8.8.8.8）")
    
    if not results.get("tcp"):
        print("⚠️  问题：TCP 连接失败")
        print("💡 建议：")
        print("   - 检查是否需要代理（设置 HTTPS_PROXY 环境变量）")
        print("   - 检查防火墙设置")
        print("   - 尝试使用 VPN")
    
    if not results.get("proxy") and not results.get("tcp"):
        print("💡 提示：如果网络受限，建议设置代理:")
        print("   export HTTPS_PROXY=http://127.0.0.1:7890  # 根据实际代理地址调整")
    
    if not results.get("api_key"):
        print("⚠️  问题：API Key 未设置")
        print("💡 建议：设置 GEMINI_API_KEY 环境变量")
    
    if results.get("api_key") and not results.get("gemini_api"):
        print("⚠️  问题：Gemini API 调用失败")
        print("💡 建议：")
        print("   - 检查 API Key 是否有效")
        print("   - 检查网络连接")
        print("   - 检查 API 配额")
    
    if results.get("api_key") and results.get("imagen_api") is False:
        print("⚠️  问题：Imagen API 端点不可用")
        print("💡 注意：Imagen API 可能需要特殊权限或不同的端点")
    
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  诊断被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 诊断过程出现异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
