# 第1天 用 Python 调用 DeepSeek

今天完成一次 Python 请求，并能解释请求内容、模型回答和结果记录。全程使用现有 Windows 和 Python，不需要安装额外 Python 库。先做离线练习，再进行一次真实调用。

本练习属于 API 与评测工程入门。后续会逐步加入工具、环境反馈和多步行动，形成智能体评测实验。这里的单道加法题用于检查流程，不能支撑模型能力或论文贡献的结论。

准备状态：离线演示已经运行通过；请求、报错处理和凭证不落盘已经使用模拟网络检查。尚未使用你的密钥验证真实 API，真实调用由你在本地完成。

## 1 打开项目

在 PowerShell 中执行：

```powershell
Set-Location -LiteralPath 'D:\科研工作区\03_Experiments\code\agent-eval-starter'
```

打开 [day01_deepseek.py](D:/科研工作区/03_Experiments/code/agent-eval-starter/day01_deepseek.py)，先只读三个位置：

- `QUESTION`：准备交给模型的题目。
- `build_request()`：把模型名称、消息和输出限制组织为字典，发送时转成 JSON。
- `record`：把本次设置、输入、输出和检查结果保存下来。

## 2 先跑离线演示

```powershell
python .\day01_deepseek.py
```

此命令不联网，不读取密钥，不产生 API 费用。屏幕会明确显示“离线模拟”。返回的 `42` 是手写示例，用来练习理解 JSON 结构，不是真实模型输出。

程序会打印 `record.json` 的完整保存位置，默认位于 `D:\科研工作区\03_Experiments\runs\` 下的独立运行目录。用文本编辑器打开该文件，找到：

| 字段 | 含义 |
| --- | --- |
| `mode` | `offline_demo` 是模拟；`live_api` 是真实调用 |
| `request.messages` | 发给模型的消息 |
| `response.choices[0].message.content` | 接口返回的回答；离线模式下为手写示例 |
| `expected` | 预先写定的参考答案 |
| `exact_match` | 去掉回答首尾空白后，是否与参考答案完全一致 |
| `request.model` / `returned_model` | 请求使用的模型名称与接口返回的模型标识 |
| `code_sha256` | 本次使用的代码文件指纹 |

## 3 再进行一次真实调用

默认配置针对 DeepSeek 官方的 OpenAI 兼容接口：`https://api.deepseek.com`，模型名为核对官方文档时使用的 `deepseek-flash`。

确认你的软件中使用的接口地址也是这一地址后，执行：

```powershell
python .\day01_deepseek.py --live
```

程序会在你的电脑上提示输入密钥。输入不显示字符；粘贴后按回车即可。密钥不会保存到程序或实验记录中。如果当前进程已设置 `DEEPSEEK_API_KEY`，程序会读取该变量。

真实调用最多发送一次请求，输出限制为 128 tokens，关闭思考模式，不自动重试。发生错误也会保存不含密钥的简要记录。客户端超时不代表服务端一定未处理请求，出现超时后先查看平台用量，再决定是否手动重跑。

如果你使用的是中转服务，需要把 `--base-url` 和 `--model` 改成该软件配置中对应的值。只更换地址不会自动解决所有兼容差异；接口必须支持这里使用的 Chat Completions 格式与参数。不要把密钥写在命令参数或学习笔记里。

模型名称可能随服务更新而变化。需要时可用程序的 `--model` 参数指定你账号支持的名称；记录中的模型标识也不等于服务方公开了具体权重版本。

## 4 今天的 Git 练习

这个目录已初始化为本地 Git 仓库。执行：

```powershell
git status
```

先理解三个概念：工作目录保存当前文件，暂存区保存准备提交的修改，提交保存一个版本。今天只观察 `git status` 输出，并找到代码文件与 `.gitignore`。下一步再练习暂存、比较修改和提交。

## 5 做完后回答三个问题

1. `messages`、`model` 和密钥分别起什么作用？为什么密钥不放进实验记录？
2. 你打开的记录是模拟还是实测？从哪个字段判断？
3. 如果回答是“答案是42”，`exact_match` 会怎样？这是评分规则怎样定义造成的？

请先完成离线演示，再把你对这三个问题的理解发到聊天中。如果进行真实调用，只分享回答、模型名称或已隐去凭证的报错。

## 参考依据

- [DeepSeek 官方首次调用说明](https://api-docs.deepseek.com/)
- [Chat Completions 参数与返回结构](https://api-docs.deepseek.com/api/create-chat-completion/)

接口说明核对时间：2026年10月28日。本地离线演示与模拟网络测试不构成真实 API 连通性验证。
