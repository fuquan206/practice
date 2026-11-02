"""第1天：一次模型调用、一次规则检查、一份可追溯的记录。

这份脚本只做一件事，但把整条链路走通了：

    准备问题 -> 组装请求 JSON -> 发送(或离线假装发送) -> 取回答
             -> 和标准答案比对 -> 把全过程写进 record.json

阅读顺序建议：
    1. 先看下面的常量 QUESTION / EXPECTED，知道"问什么、答案是什么"。
    2. 再看 build_request()，知道"发给模型的请求长什么样"。
    3. 然后看 main() 里 live 与 else 两个分支，知道"真实调用"和"离线模拟"的区别。
    4. 最后看 record 字典和文件保存部分，知道"为什么这个实验可追溯"。

默认不加参数运行时是离线模拟：不联网、不读密钥、不花钱。
加 --live 才会真正请求 DeepSeek 接口。
"""

# ---- 标准库导入：全部是 Python 自带的，不需要 pip install ----
import argparse                 # 解析命令行参数，例如 --live / --model
from datetime import datetime, timezone  # 生成带时区的时间戳，用于 run_id 和记录
from getpass import getpass     # 在终端里输入密钥时不回显字符
import hashlib                  # 计算代码文件的 SHA-256 指纹
import json                     # 请求体和记录都序列化为 JSON
import os                       # 读取环境变量 DEEPSEEK_API_KEY
from pathlib import Path        # 跨平台地拼接路径、创建目录、读写文件
import time                     # 用 perf_counter 做高精度计时
from urllib.error import HTTPError, URLError  # HTTP 状态码错误 / 网络层错误
from urllib.parse import urlsplit             # 拆解 URL，检查协议、主机、是否含密钥等
from urllib.request import HTTPRedirectHandler, Request, build_opener  # 标准库的 HTTP 客户端


# ---- 常量：整个脚本的"实验设定"都集中在这里 ----

# __file__ 是当前脚本的路径；.resolve() 转成绝对路径；.parent 取所在目录
# 也就是 D:\科研工作区\03_Experiments\code\agent-eval-starter
PROJECT_DIR = Path(__file__).resolve().parent

# 再往上两级：agent-eval-starter -> code -> 03_Experiments，然后拼上 runs
# 结果：D:\科研工作区\03_Experiments\runs，所有实验记录都放这里，不污染代码目录
DEFAULT_RUNS = PROJECT_DIR.parent.parent / "runs"

# 发给模型的问题。要求"只回答一个整数"，是为了让后面的精确比对变得简单
QUESTION = "请计算 17 + 25。只回答一个整数，不要解释。"

# 预先写死的标准答案。注意它是字符串 "42" 而不是数字 42，
# 因为模型返回的文本天然是字符串，比对时两边类型要一致
EXPECTED = "42"


class NoRedirect(HTTPRedirectHandler):
    """禁止自动跟随 HTTP 重定向。

    urllib 默认遇到 301/302/307/308 会自动跳到新地址，而且会把
    Authorization 头一起带过去 —— 这等于把密钥发给了一个未知服务器。
    这里重写 redirect_request() 并返回 None，意思是"不要跳转"，
    于是 3xx 会变成 HTTPError 被下面的异常处理捕获。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 携带密钥的请求不自动跳转到其他地址。
        return None


def build_request(model):
    """组装"请求体"字典 —— 也就是最终要 POST 给接口的那段 JSON。

    关键点：密钥不在这里。密钥是放在 HTTP 头 Authorization 里的，
    请求体只描述"让模型做什么"，两者分开，记录里才能安心保存请求体。
    """
    return {
        # 用哪个模型。名字由服务方定义，可能随服务更新而变化
        "model": model,

        # 对话消息列表。role 表示说话人：user=用户，assistant=模型
        # 这里只有一条用户消息，就是上面的 QUESTION
        "messages": [{"role": "user", "content": QUESTION}],

        # 关闭"思考模式"，让输出更短更稳定，也避免额外的推理 token 开销
        "thinking": {"type": "disabled"},

        # False = 一次性返回完整结果；True 则是流式逐字返回，处理起来更复杂
        "stream": False,

        # 最多生成 128 个 token。这是成本上限，也是防止模型长篇大论
        "max_tokens": 128,

        # 温度 0 表示尽量确定、少随机。但要注意：这不等于 100% 可复现
        "temperature": 0,
    }


def hide_secret(value, secret):
    """递归地把数据里出现的密钥替换成 [REDACTED]。

    为什么需要？正常情况下密钥只在 HTTP 头里，不会出现在响应中。
    但万一服务端出错时把请求头原样回显，密钥就会跟着 response 一起
    被写进 record.json。这个函数是一道兜底。

    实现方式：根据类型递归 —— 字符串就替换，列表逐项处理，
    字典对键和值都处理，其他类型（数字、None 等）原样返回。
    """
    if isinstance(value, str):
        # 必须先判断 secret 非空：空字符串能匹配任意两个字符之间的位置，
        # "abc".replace("", "-") 会得到 "-a-b-c-"，从而把正文全部破坏。
        # 离线运行时 secret 正好是空串，所以这个判断不是可有可无的优化。
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, list):
        return [hide_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {hide_secret(k, secret): hide_secret(v, secret) for k, v in value.items()}
    return value


def main(argv=None):
    # description=__doc__ 会把文件开头的说明文字当作 --help 的描述
    # argv=None 时 argparse 自动读取命令行；测试时可以传入列表模拟参数
    parser = argparse.ArgumentParser(description=__doc__)

    # action="store_true"：命令行带了 --live 就是 True，不带就是 False
    parser.add_argument("--live", action="store_true", help="真实调用一次；默认离线模拟")
    parser.add_argument("--model", default="deepseek-flash", help="接口接受的模型名称")
    parser.add_argument("--base-url", default="https://api.deepseek.com", help="软件中使用的 OpenAI 兼容接口地址")
    # type=Path：argparse 会自动把字符串转成 Path 对象
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RUNS, help="练习记录目录")
    args = parser.parse_args(argv)

    # ---- 第一步：校验并规范化接口地址 ----
    # strip() 去掉首尾空格，rstrip("/") 去掉末尾斜杠，避免拼出 //chat/completions
    base_url = args.base_url.strip().rstrip("/")
    parsed = urlsplit(base_url)

    # 逐项检查：
    #   scheme 必须是 https（否则密钥会明文传输）
    #   netloc 是主机名，不能为空
    #   username/password 不允许出现在 URL 里（那等于把密钥写进命令行）
    #   query/fragment 也不允许，避免地址被塞入额外参数
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("接口地址需要使用 HTTPS，且不应包含密钥、用户名、查询参数或片段。")

    # 允许用户直接给完整端点，也允许只给域名，这里统一补全成 .../chat/completions
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"

    # ---- 第二步：组装请求、生成本次运行的唯一标识 ----
    payload = build_request(args.model)

    # mode 会写进记录，用来区分"真实调用"和"离线模拟"
    mode = "live_api" if args.live else "offline_demo"

    # 用 UTC 时间而不是本地时间，便于跨时区、跨机器比较
    started_at = datetime.now(timezone.utc)

    # run_id 形如 20261029T083015_123456Z_deepseek_day01_offline_demo
    # 精确到微秒，保证每次运行都有独立目录，不会互相覆盖
    run_id = started_at.strftime("%Y%m%dT%H%M%S_%fZ") + "_deepseek_day01_" + mode

    # ---- 第三步：先建好"实验记录"骨架，边跑边往里填 ----
    record = {
        "run_id": run_id,
        "mode": mode,
        "started_at_utc": started_at.isoformat(),
        "task_id": "addition_001",          # 任务编号，方便以后多任务管理
        "data_split": "learning_example",   # 数据划分：这是学习示例，不是测试集
        "random_seed": None,                # 本实验不涉及随机采样，故为空
        # 代码指纹：读取本文件字节并算 SHA-256。
        # 以后再看到这条记录，能确认它是由哪个版本的代码产生的
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "endpoint": endpoint,               # 记录请求发往哪里
        "request": payload,                 # 记录发了什么
        "expected": EXPECTED,               # 记录标准答案
        "response": None,                   # 待填：模型原始返回
        "error": None,                      # 待填：出错时的简要说明
    }

    # 密钥先置空；只有 live 模式才会被赋值
    secret = ""
    # perf_counter 是单调递增的高精度计时器，适合测"耗时"
    started_clock = time.perf_counter()

    # ---- 第四步：真实调用 或 离线模拟 ----

    if args.live:
        # 优先从环境变量读密钥（适合临时设置，不必每次手输）
        secret = os.environ.get("DEEPSEEK_API_KEY", "").strip()

        if not secret:
            # 环境变量没有，就交互式询问。getpass 输入时不显示字符
            try:
                secret = getpass("请在本地输入 API 密钥（输入不显示，也不会保存）：").strip()
            except (EOFError, KeyboardInterrupt):
                # Ctrl+C 或输入流结束，视为用户主动取消
                print("已取消，未发送请求。")
                return 1

        if not secret:
            # 用户直接回车，说明密钥为空，不该发请求
            print("密钥为空，未发送请求。")
            return 1

        print("真实调用：最多发送一次请求，不自动重试；输出上限为 128 tokens。")

        # Request 对象 = 一次 HTTP 请求的全部信息
        #   data：请求体，json.dumps 转字符串后再 encode 成 UTF-8 字节
        #         ensure_ascii=False 让中文保持原样而不是 \uXXXX
        #   headers：Content-Type 声明 JSON；Authorization 放密钥
        #            "Bearer " 是这类接口约定的前缀
        #   method：POST
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret},
            method="POST",
        )

        # 从这里开始重新计时，让 elapsed_ms 只反映网络往返，不含用户输密钥的时间
        started_clock = time.perf_counter()
        try:
            # build_opener(NoRedirect()) 构造一个"不自动跳转"的客户端
            # with 语句保证连接在用完后自动关闭
            # timeout=60 表示 60 秒内没有响应就放弃
            with build_opener(NoRedirect()).open(request, timeout=60) as response:
                record["http_status"] = response.status          # 通常是 200
                # 读取响应体 -> 按 UTF-8 解码 -> 解析成 Python 字典
                record["response"] = json.loads(response.read().decode("utf-8"))

        except HTTPError as error:
            # HTTPError 是"服务器有回应，但状态码不是 2xx"
            # 不打印服务端错误正文，避免把凭证等敏感内容带入日志。
            explanations = {301: "接口要求跳转，请核对准确的接口地址。", 302: "接口要求跳转，请核对准确的接口地址。", 307: "接口要求跳转，请核对准确的接口地址。", 308: "接口要求跳转，请核对准确的接口地址。", 401: "认证失败，请检查密钥与接口地址是否配套。", 402: "余额不足。", 404: "接口路径或模型名称可能不匹配。", 429: "触发限流。"}
            # 只记录状态码 + 一句人话解释；看不懂的码给通用提示
            record["error"] = {"type": "http_error", "status": error.code, "message": explanations.get(error.code, "服务端返回错误，请核对接口文档。")}

        except (URLError, TimeoutError, OSError):
            # 这几类是"根本没拿到有效回应"：DNS 失败、断网、超时等
            record["error"] = {"type": "connection_error", "message": "连接或读取失败；本程序未自动重试，请先检查网络和接口地址。"}

        except (ValueError, UnicodeError):
            # 拿到了回应，但内容不是合法 JSON 或无法按 UTF-8 解码
            record["error"] = {"type": "invalid_json", "message": "接口返回的内容不是可读取的 JSON。"}

    else:
        # 离线分支：不联网、不读密钥，硬编码一个"假响应"
        print("【离线模拟】未连接任何模型服务，没有真实模型输出，也没有 API 费用。")
        record["response"] = {
            # id 和 model 故意写成一眼能认出是假的名字，防止被误当成真实结果
            "id": "handwritten-demo-only",
            "model": "handwritten-fixture-not-a-model",
            # 结构刻意模仿真实接口：choices 是列表，取第 0 个，
            # 里面有 message.content（回答正文）和 finish_reason（结束原因）
            "choices": [{"message": {"role": "assistant", "content": "42"}, "finish_reason": "stop"}],
            "usage": None,   # 真实接口这里会给 token 用量，模拟时留空
        }

    # 记录耗时：当前时刻减去起始时刻，乘 1000 换成毫秒，保留两位小数
    record["elapsed_ms"] = round((time.perf_counter() - started_clock) * 1000, 2)

    # ---- 第五步：从响应里取出回答，并按规则打分 ----

    # 只有真的拿到了响应（离线分支或成功的 live 调用）才解析
    if record["response"] is not None:
        try:
            # 按真实接口的层级逐层取值：response -> choices[0] -> message -> content
            first_choice = record["response"]["choices"][0]
            content = first_choice["message"]["content"]

            # 如果 content 不是字符串（例如 None 或列表），说明结构不符合预期
            if not isinstance(content, str):
                raise ValueError("无文本回答")

            # strip() 去掉首尾空白和换行，避免 " 42\n" 这种差异影响判分
            record["answer"] = content.strip()

            # 评分规则：字符串完全相等才算对。
            # 所以 "答案是42" 会判错 —— 规则就是这么定的
            record["exact_match"] = record["answer"] == EXPECTED

            # 附带的诊断信息：为什么停下（stop / length 等）、服务端实际用的模型
            record["finish_reason"] = first_choice.get("finish_reason")
            record["returned_model"] = record["response"].get("model")

        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            # 任何一层缺字段、类型不对、列表为空，都会落到这里。
            # 注意：原始 response 仍然会被保存，方便事后排查
            record["error"] = {"type": "unexpected_response", "message": "返回结构中没有预期的文本回答，请检查已保存的 response。"}

    # ---- 第六步：落盘保存 ----

    # 保存前最后一道保险：把可能混进记录里的密钥替换掉
    record = hide_secret(record, secret)

    # 每次运行一个独立目录：<output-root>/<run_id>/
    run_dir = args.output_root.resolve() / run_id

    # parents=True 允许逐级创建父目录；exist_ok=False 表示目录已存在就报错，
    # 从而保证不会把两次运行的结果混在同一个目录里
    run_dir.mkdir(parents=True, exist_ok=False)

    record_path = run_dir / "record.json"

    # ensure_ascii=False 保留中文可读性；indent=2 让 JSON 便于人工查看
    # encoding="utf-8" 明确指定编码，避免 Windows 默认编码问题
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 第七步：在屏幕上给出结论 ----

    if record["error"]:
        # 出错也要告诉用户记录在哪，方便查看细节
        print(record["error"]["message"])
        print("记录已保存：", record_path)
        return 1    # 非零退出码 = 本次运行失败

    print("发送的问题：", QUESTION)
    print("收到的回答：", record["answer"])
    print("与预期答案完全一致：", record["exact_match"])
    print("记录已保存：", record_path)

    if not args.live:
        # 明确提醒：离线模式的 42 是手写的，不能拿来评价模型
        print("模拟答案为手写示例；以上结果不代表真实模型能力。")

    return 0        # 0 = 成功


# 只有"直接运行这个文件"时，下面的代码才会执行；
# 如果被别的文件 import，则不会自动跑 main()
if __name__ == "__main__":
    # main() 返回 0/1，交给 SystemExit 作为进程退出码
    raise SystemExit(main())
