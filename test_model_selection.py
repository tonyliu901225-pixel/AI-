#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试模型自动选择功能
"""

import os
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
import google.generativeai as genai


def test_model_listing():
    """测试列出可用模型"""
    print("=" * 80)
    print("🔍 测试模型列表查询")
    print("=" * 80)
    
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("❌ GEMINI_API_KEY 未设置")
        return False
    
    try:
        # 配置 API
        genai.configure(api_key=api_key, transport="rest")
        print("✅ API 配置成功\n")
        
        # 列出模型（带超时）
        print("🔍 正在查询可用模型（最多等待 30 秒）...")
        import signal
        
        models_found = []
        timeout_occurred = False
        
        def timeout_handler(signum, frame):
            nonlocal timeout_occurred
            timeout_occurred = True
            raise TimeoutError("查询模型列表超时（30秒）")
        
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(30)
        
        try:
            t0 = time.time()
            for m in genai.list_models():
                if timeout_occurred:
                    break
                name = getattr(m, "name", "") or ""
                if name:
                    if name.startswith("models/"):
                        name = name.split("/", 1)[1]
                    methods = getattr(m, "supported_generation_methods", None) or []
                    has_generate_content = "generateContent" in methods if methods else False
                    
                    models_found.append({
                        "name": name,
                        "has_generate_content": has_generate_content,
                        "methods": list(methods) if methods else []
                    })
            signal.alarm(0)
            dt = time.time() - t0
            
            print(f"✅ 查询完成（耗时: {dt:.2f} 秒）")
            print(f"📋 找到 {len(models_found)} 个模型\n")
            
            # 显示模型列表
            print("可用模型列表：")
            print("-" * 80)
            for i, model in enumerate(models_found[:20], 1):  # 只显示前20个
                icon = "✅" if model["has_generate_content"] else "❌"
                print(f"{i:2d}. {icon} {model['name']}")
                if model["has_generate_content"]:
                    print(f"     支持方法: {', '.join(model['methods'][:3])}")
            if len(models_found) > 20:
                print(f"\n... 还有 {len(models_found) - 20} 个模型未显示")
            
            # 推荐模型
            print("\n" + "-" * 80)
            print("推荐使用的模型（支持 generateContent）：")
            recommended = [m for m in models_found if m["has_generate_content"]]
            
            priority_keywords = [
                "2.0-flash-exp",
                "2.0-flash",
                "2.0-pro",
                "1.5-flash",
                "1.5-pro",
                "flash",
                "pro",
            ]
            
            recommended_sorted = []
            for kw in priority_keywords:
                for m in recommended:
                    if kw in m["name"].lower() and m not in recommended_sorted:
                        recommended_sorted.append(m)
            
            # 添加剩余的
            for m in recommended:
                if m not in recommended_sorted:
                    recommended_sorted.append(m)
            
            for i, model in enumerate(recommended_sorted[:5], 1):
                print(f"  {i}. {model['name']}")
            
            return True
            
        except TimeoutError:
            signal.alarm(0)
            print("❌ 查询模型列表超时（可能网络问题）")
            print("💡 建议：检查网络连接或使用代理")
            return False
            
    except Exception as e:
        signal.alarm(0)
        print(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_model_initialization():
    """测试模型初始化（自动选择）"""
    print("\n" + "=" * 80)
    print("🔍 测试模型自动选择功能")
    print("=" * 80)
    
    try:
        from skills.illustrator import IllustratorSkill
        
        print("\n正在初始化 IllustratorSkill（会自动选择可用模型）...")
        print("⚠️  这可能需要几秒钟...\n")
        
        t0 = time.time()
        illustrator = IllustratorSkill()
        dt = time.time() - t0
        
        print(f"\n✅ 初始化成功（耗时: {dt:.2f} 秒）")
        print(f"📦 使用的模型: {illustrator.text_model_name}")
        
        return True
        
    except Exception as e:
        print(f"\n❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("🧪 模型选择功能测试")
    print("=" * 80)
    
    # 测试1: 列出可用模型
    success1 = test_model_listing()
    
    # 测试2: 测试自动选择
    if success1:
        success2 = test_model_initialization()
    else:
        print("\n⚠️  跳过模型初始化测试（模型列表查询失败）")
        success2 = False
    
    # 总结
    print("\n" + "=" * 80)
    print("📊 测试总结")
    print("=" * 80)
    print(f"模型列表查询: {'✅ 通过' if success1 else '❌ 失败'}")
    print(f"模型自动选择: {'✅ 通过' if success2 else '❌ 失败'}")
    
    if success1 and success2:
        print("\n🎉 所有测试通过！模型选择功能正常")
    else:
        print("\n⚠️  部分测试失败，请检查错误信息")
    
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 测试过程出现异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
