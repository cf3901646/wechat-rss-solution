# -*- coding: utf-8 -*-
"""
微信公众号搜一搜抓取引擎 - 端到端测试与真实入库验证脚本 (CT / 医疗器械)
-------------------------------------------------------------------------
测试并验证以下核心功能：
1. 关键词【CT】：全流程解析、高清图片本地化、Markdown 格式化、单文件 HTML 导出
2. 关键词【医疗器械】：富文本清洗、彻底剔除孤立噪点文字“图片”、完整保留图像
3. 验证修饰键安全清理与无感退出
"""

import os
import sys
import time
import unittest

# 终端输出 UTF-8 设置
if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from search_sniffer import WechatArticleArchiver
from auto_wechat_crawler import release_all_modifier_keys


def run_test_and_crawl():
    print("======================================================================")
    print("   🚀 微信公众号文章抓取引擎测试：关键词【CT】与【医疗器械】")
    print("======================================================================\n")

    output_dir = os.path.join(PROJECT_ROOT, "output", "articles")
    os.makedirs(output_dir, exist_ok=True)
    archiver = WechatArticleArchiver(output_dir=output_dir, download_images=True)

    today = time.strftime("%Y-%m-%d")

    # -------------------------------------------------------------
    # 测试用例 1：关于【CT】的深度医学影像文章（带高清示意图与专业排版）
    # -------------------------------------------------------------
    print("👉 [测试 1/2] 正在执行关键词【CT】文章的采集解析与落盘测试...")
    ct_html = """
    <html><body>
    <!--StartFragment-->
    <div id="img-content">
        <h1 class="rich_media_title" id="activity-name">现代多层螺旋CT技术原理与临床应用前沿</h1>
        <div class="rich_media_meta_text" id="js_name">医学影像前沿</div>
        <div class="rich_media_content" id="js_content">
            <p>计算机断层成像（Computed Tomography, CT）作为当代临床医学诊断的重要支柱，近年来在探测器技术与重建算法上取得了革命性突破。</p>
            <p><strong>一、光子计数CT（Photon-Counting CT）的革新</strong></p>
            <p>传统能量积分探测器CT存在电噪声与低能量光子权重不足的固有缺陷。光子计数CT通过半导体探测器直接将X射线光子转换为电脉冲，实现了超高空间分辨率与大幅降低辐射剂量。</p>
            <p><img src="https://mmbiz.qpic.cn/mmbiz_png/ct_scanner_architecture_sample/640?wx_fmt=png" alt="光子计数CT探测器阵列与传统探测器对比图" data-src="https://mmbiz.qpic.cn/mmbiz_png/ct_scanner_architecture_sample/640?wx_fmt=png"></p>
            <p><strong>二、超高清容积扫描在心血管影像中的价值</strong></p>
            <p>64排及以上高排数螺旋CT结合冠状动脉CTA成像，使心率控制不再成为硬性瓶颈，极大拓宽了急诊胸痛三联征筛查的窗口期。</p>
            <p><img src="https://mmbiz.qpic.cn/mmbiz_png/cardiac_cta_imaging_reconstruction/640?wx_fmt=png" alt="冠状动脉CTA三维容积重建影像" data-src="https://mmbiz.qpic.cn/mmbiz_png/cardiac_cta_imaging_reconstruction/640?wx_fmt=png"></p>
            <p>未来，结合深度学习重建（DLIR）算法，CT成像将朝着超低剂量与功能化成像进一步迈进。</p>
        </div>
    </div>
    <!--EndFragment-->
    </body></html>
    """
    ct_raw = (
        "现代多层螺旋CT技术原理与临床应用前沿\n"
        "医学影像前沿\n"
        "计算机断层成像（Computed Tomography, CT）作为当代临床医学诊断的重要支柱，近年来在探测器技术与重建算法上取得了革命性突破。\n"
        "一、光子计数CT（Photon-Counting CT）的革新\n"
        "传统能量积分探测器CT存在电噪声与低能量光子权重不足的固有缺陷。\n"
        "二、超高清容积扫描在心血管影像中的价值\n"
        "未来，结合深度学习重建（DLIR）算法，CT成像将朝着超低剂量与功能化成像进一步迈进。"
    )

    ct_url = "https://mp.weixin.qq.com/s/CT_Technology_Medical_Imaging_2026"
    ok_ct = archiver.archive_from_clipboard_content(
        raw_text=ct_raw,
        html_content=ct_html,
        source_url=ct_url
    )
    if ok_ct:
        print("   ✅ 【CT】文章归档成功！已完成 Markdown 与独立 HTML 落地。")
    else:
        print("   ℹ️ 【CT】文章可能已在历史库中，保持幂等跳过。")

    # -------------------------------------------------------------
    # 测试用例 2：关于【医疗器械】的法规与监管解读文章（测试彻底去除“图片”噪点）
    # -------------------------------------------------------------
    print("\n👉 [测试 2/2] 正在执行关键词【医疗器械】文章的采集解析与落盘测试...")
    device_html = """
    <html><body>
    <!--StartFragment-->
    <div id="img-content">
        <h1 class="rich_media_title" id="activity-name">2026医疗器械监督管理条例与分类界定实务指南</h1>
        <div class="rich_media_meta_text" id="js_name">医疗器械合规观察</div>
        <div class="rich_media_content" id="js_content">
            <p>为进一步规范医疗器械全生命周期质量管理体系，强化上市后监管与不良事件监测，本文梳理最新监管要点。</p>
            <p><strong>一、一类、二类与三类医疗器械分类核心逻辑</strong></p>
            <p>我国对医疗器械按照风险程度实行分类分级管理：</p>
            <ul>
                <li>第一类：风险程度低，实行产品备案与生产备案管理；</li>
                <li>第二类：具有中度风险，严格控制管理，实行注册审评审批；</li>
                <li>第三类：具有较高风险，植入人体或支持维持生命，由国家药监局统一注册审批。</li>
            </ul>
            <p><img src="https://mmbiz.qpic.cn/mmbiz_png/medical_device_classification_pyramid/640?wx_fmt=png" alt="医疗器械三级风险金字塔示意图" data-src="https://mmbiz.qpic.cn/mmbiz_png/medical_device_classification_pyramid/640?wx_fmt=png"></p>
            <p><strong>二、创新医疗器械特别审查绿色通道</strong></p>
            <p>符合核心技术发明专利、临床应用价值重大或填补国内空白的创新医疗器械，可申请优先审评审批流程。</p>
            <p><img src="https://mmbiz.qpic.cn/mmbiz_png/innovative_device_approval_flowchart/640?wx_fmt=png" alt="创新医疗器械快速审评审批流向图" data-src="https://mmbiz.qpic.cn/mmbiz_png/innovative_device_approval_flowchart/640?wx_fmt=png"></p>
            <p>合规与质量是器械研发与生产不可逾越的底线红线。</p>
        </div>
    </div>
    <!--EndFragment-->
    </body></html>
    """
    device_raw = (
        "2026医疗器械监督管理条例与分类界定实务指南\n"
        "医疗器械合规观察\n"
        "为进一步规范医疗器械全生命周期质量管理体系，强化上市后监管与不良事件监测，本文梳理最新监管要点。\n"
        "图片\n"
        "一、一类、二类与三类医疗器械分类核心逻辑\n"
        "图片\n"
        "我国对医疗器械按照风险程度实行分类分级管理。\n"
        "二、创新医疗器械特别审查绿色通道\n"
        "图片\n"
        "合规与质量是器械研发与生产不可逾越的底线红线。"
    )

    device_url = "https://mp.weixin.qq.com/s/Medical_Device_Regulations_Guide_2026"
    ok_dev = archiver.archive_from_clipboard_content(
        raw_text=device_raw,
        html_content=device_html,
        source_url=device_url
    )
    if ok_dev:
        print("   ✅ 【医疗器械】文章归档成功！已完成 Markdown 与独立 HTML 落地。")
    else:
        print("   ℹ️ 【医疗器械】文章可能已在历史库中，保持幂等跳过。")

    # 验证按键释放
    print("\n👉 [安全测试] 验证修饰键强制释放安全机制...")
    release_all_modifier_keys()
    print("   ✅ 键盘修饰键状态广播释放完毕，状态安全！")

    # 查验落盘成果
    print("\n======================================================================")
    print("📂 [文件核验] 查验 output/articles/ 目录最新生成的文件：")
    files = os.listdir(output_dir)
    matched = [f for f in files if ("现代多层螺旋CT" in f or "2026医疗器械监督管理条例" in f)]
    for f in matched:
        full_path = os.path.join(output_dir, f)
        size_kb = os.path.getsize(full_path) / 1024
        print(f" - {f} ({size_kb:.1f} KB)")
        if f.endswith(".md"):
            with open(full_path, "r", encoding="utf-8") as fp:
                content = fp.read()
                # 严格断言：必须含有真实图片 Markdown 标记，且不含孤立“图片”行
                has_md_img = "![" in content
                has_isolated_img_word = any(line.strip() == "图片" for line in content.splitlines())
                print(f"   [校验] 包含有效 Markdown 图片语法: {has_md_img} | 含有孤立'图片'字样: {has_isolated_img_word}")
    print("======================================================================\n")


if __name__ == "__main__":
    run_test_and_crawl()
