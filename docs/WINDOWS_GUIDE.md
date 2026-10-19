# Windows 本地自动化拉取与定时任务指南

如果你平时习惯在 Windows 电脑上使用 Obsidian、Logseq 或将文章归档在本地盘，你可以让 Windows 电脑每天定时自动从已有的 RSS 源抓取新文章并保存为 Markdown。

---

## 零、 前置：安装依赖到项目虚拟环境

同步引擎依赖 `PyYAML`、`requests`、`beautifulsoup4`，必须先装好。
`start_sync.bat` 和定时任务注册脚本都会优先使用项目内的 `.venv`，所以请装进 `.venv`，
**不要只装到全局 Python**：

```powershell
# 在项目根目录执行
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 一、 快速单次测试运行

1. 打开本项目根目录下的 `config\config.yaml`，填入你的 RSS 订阅源地址，例如：
   ```yaml
   rss_feeds:
     - name: "我的精选公众号"
       url: "http://<你的VPS公网IP>:8001/feeds/all.xml" # 填入你的 we-mp-rss 导出的 RSS 链接
       enabled: true
   storage:
     # 可以直接写成你的 Obsidian 库路径，例如：
     output_dir: "D:/MyObsidianVault/微信公众号文章"
     download_images: true
   ```
2. 双击运行 `windows\start_sync.bat`，即可看到黑框终端开始拉取并显示进度，拉取完毕后自动退出。

---

## 二、 设置 Windows 每天定时自动静默运行（无黑框弹窗）

利用 Windows 内置的【任务计划程序】（Task Scheduler），无需人工开机手动点，到点自动静默拉取。

### 方法 A：一键 PowerShell 注册（推荐）

1. 按 `Win + X` 键，选择 **“终端(管理员)”** 或 **“Windows PowerShell(管理员)”**。
2. 运行项目提供的自动注册脚本：
   ```powershell
   # 进入本项目的 windows 目录
   cd "C:\Users\...\wechat-rss-solution\windows"

   # 执行注册脚本（默认每天凌晨 02:00 静默执行）
   .\setup_scheduled_task.ps1

   # 如果你想改成每隔 4 小时执行一次，执行：
   # .\setup_scheduled_task.ps1 -IntervalHours 4
   ```
3. 注册成功后，任务会自动以后台静默方式运行（使用项目 `.venv\Scripts\pythonw.exe`，不会弹出黑色窗口干扰日常使用）。

脚本在注册前会做两项检查，任一不通过就直接报错退出，不会留下一个注定跑不起来的任务：

* `config\config.yaml` 是否存在；
* 选定的解释器能否 `import yaml, requests, bs4`。

如果项目内没有 `.venv`，脚本会退回系统 PATH 上的 Python 并给出警告 —— 此时请务必确认
该解释器装了 `requirements.txt` 里的依赖，否则任务会**静默失败**（静默运行没有窗口，
看不到任何报错）。

### 方法 B：图形界面手动创建

1. 按 `Win + R` 键，输入 `taskschd.msc` 打开“任务计划程序”。
2. 点击右侧的【创建任务】：
   * **常规**：名称填写 `WechatRssSync`，勾选“使用最高权限运行”。
   * **触发器**：新建触发器，设置每天执行（例如每天晚上 23:00）。
   * **操作**：新建操作，选择“启动程序”：
     * 程序或脚本：填写项目内 `.venv\Scripts\pythonw.exe` 的**完整路径**
       （不要用系统 Python，依赖装在 `.venv` 里）。
     * 添加参数：填写 `scripts\rss_sync.py --config config\config.yaml`。
     * 起始于：填写本项目的绝对根目录路径。
3. 点击确定保存即可。

---

## 三、 查看与管理定时任务

* **手动立即测试**：在“任务计划程序”中找到 `WechatRssSyncTask`，右键点击【运行】。
* **查看历史同步记录**：打开 `output/sync_history.json`，里面记录了所有已同步文章的标题、原文链接和保存时间。
* **卸载定时任务**：以管理员身份运行 `windows\remove_scheduled_task.ps1` 即可。

---

## 四、 排查：任务跑了但没有新文章

静默运行没有控制台，**唯一的排查入口是日志文件**：

```text
logs\sync.log
```

每次运行开头会打印实际生效的三个路径，先核对它们是否和你预期一致：

```text
2026-09-22 02:00:01 [INFO] 配置文件: C:\...\wechat-rss-solution\config\config.yaml
2026-09-22 02:00:01 [INFO] Markdown 输出目录: C:\...\wechat-rss-solution\output\articles
2026-09-22 02:00:01 [INFO] 历史记录文件: C:\...\wechat-rss-solution\output\sync_history.json
```

常见情况：

* 日志里报 `ModuleNotFoundError` → 定时任务用的解释器没装依赖，重新运行
  `setup_scheduled_task.ps1`（它会做依赖自检）。
* 日志显示解析出 N 篇但新增 0 篇 → 这些文章的 guid 已在 `sync_history.json` 里，
  属于正常去重。想重新抓取加 `--force`。
* 完全没有日志文件 → 任务没有真正执行，去“任务计划程序”的【历史记录】选项卡看上次运行结果。

> 配置里的相对路径一律以**项目根目录**为基准，与“起始于”填的工作目录无关。
> 想把文章直接存进 Obsidian 库，在 `output_dir` 写绝对路径即可。
