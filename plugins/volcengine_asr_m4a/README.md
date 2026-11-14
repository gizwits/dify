# 火山 ASR：M4A 兼容版

基于官方 `langgenius/volcengine_maas` 的 ASR 实现提取、维护的独立模型插件。
用于「App 长按录音 → 松手上传 → 返回识别文本」；不会在录音期间向 App 返回中间字幕。
支持新版语音 API Key 和旧版 App ID / Access Token。

English: A speech-to-text-only fork of the official Volcengine provider. Decodes uploaded
M4A recordings on the server and sends normalized PCM to Volcengine sentence recognition.
The plugin has a separate identity and can coexist with the official provider.

## 安装

1. 在 Dify「插件 → 安装插件 → 通过本地插件文件安装」选择 `gizwits-volcengine_asr_m4a-0.1.0.difypkg`。
2. 这是自维护插件，没有 Dify Marketplace 官方签名。如果自部署的 plugin-daemon 配置为
   `FORCE_VERIFYING_SIGNATURE=true`，会拒绝安装。管理员需要先按自定义插件管理策略允许本地插件；
   本项目不会自动修改该开关。
3. 在「设置 → 模型供应商 → 火山语音识别（M4A 兼容）」添加模型，类型为语音转文字，
   模型名称可填 `doubao-seed-asr`。
4. 新版控制台选择 API Key，填**豆包语音控制台的 API Key**；不是方舟 LLM 的 Endpoint ID 或 IAM AK/SK。
   旧版选择 App ID + Access Token，填写语音服务的对应凭据。
5. Resource ID 选择账号已经开通的资源。默认 `volc.seedasr.sauc.duration`（ASR 2.0 小时版），
   也可选择 ASR 1.0 或并发版。保存模型时会使用随包的短 WAV 验证凭据，产生一次识别调用。
6. 将此模型设为工作区默认语音转文字模型，在应用中开启语音转文字并发布。

插件依赖通过 Python wheels 安装，需能访问 PyPI。PyAV 包含解码库，不需要额外安装系统 `ffmpeg`，
也不需要更换 plugin-daemon 镜像。支持 Python 3.12、Linux amd64/arm64。

## App 调用

仍调用 Dify `POST /v1/audio-to-text`，使用 multipart/form-data 的 `file` 和 `user` 字段。
文件可以保持 M4A，文件部分的 Content-Type 可为 `audio/m4a` 或 `audio/x-m4a`。
`audio/x-m4a` 需要 API 部署本项目的兼容提交 `42f5da075d`（已纳入本次 1.16.3-api 构建）。
不要把整个 multipart 请求的 Content-Type 设置成音频类型。

```sh
curl "$DIFY_BASE_URL/v1/audio-to-text" \
  -H "Authorization: Bearer $DIFY_APP_API_KEY" \
  -F 'file=@recording.m4a;type=audio/x-m4a' \
  -F 'user=user-123'
```

成功返回 `{"text":"识别结果"}`，之后由 App 发起聊天请求。
本插件只有 ASR；TTS 继续安装、配置独立 TTS 模型插件。

## 格式与边界

- 按文件内容识别 M4A/AAC、WAV、MP3、AMR；即使 Dify 把缓冲区命名为 `temp.mp3` 也能处理 M4A。
- 服务端解码、重采样为 16 kHz、单声道、16 位小端 PCM。
- 显式命名为 `.pcm` 的裸 PCM 输入必须已是上述格式；Dify 通用上传 API 本身不接受 `audio/pcm`。
- 上传最大 25 MiB，解码后最长 300 秒；无法解码、超限或缺少音轨会在调用火山前失败。
- 固定调用 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream`，启用 TLS 校验。
- 返回最终结果后才结束；中途断开会报错，不把中间结果作为成功返回；不自动重试付费调用。

## 开发、测试和打包

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt pytest
.venv/bin/python -m pytest -q
dify plugin package . -o gizwits-volcengine_asr_m4a-0.1.0.difypkg
```

测试使用合成的真实 48 kHz 双声道 AAC/M4A 文件及模拟火山协议响应，不需要供应商密钥。
测试不能代替真实账号的资源权限和识别效果验收。安装后请用一段真实 App 录音试用。
`tests/fixtures/stereo-48k.m4a` 为 FFmpeg 生成的 0.4 秒 440 Hz 正弦波，无真人录音。

## 上游

- 来源：<https://github.com/langgenius/dify-official-plugins/tree/60804e30f6fa41fbe890c02c9849b0bdbe28d13a/models/volcengine_maas>
- 许可证：Apache-2.0，见 `LICENSE`；修改记录见 `NOTICE`。
- 火山一句话识别协议：<https://docs.volcengine.com/docs/doubao-voice/unidirectional-streaming-automatic-speech-recognition-websocket?lang=zh>

独立插件 ID 为 `gizwits/volcengine_asr_m4a`，更新官方 `langgenius/volcengine_maas` 不会覆盖此兼容版。
