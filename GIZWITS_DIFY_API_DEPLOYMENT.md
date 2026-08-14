# Gizwits Dify API 镜像部署指南

本文档用于将基于 Dify 官方源码定制的 API 镜像手动构建、推送至火山引擎镜像仓库，并部署到 VKE 集群。

部署不经过 Jenkins，所有构建和推送操作均在本地手动执行。

## 1. 镜像信息

火山引擎镜像仓库：

```text
iot-cn-beijing.cr.volces.com
```

命名空间：

```text
gizwits_platform
```

镜像名称：

```text
dify-api
```

完整镜像地址格式：

```text
iot-cn-beijing.cr.volces.com/gizwits_platform/dify-api:<tag>
```

## 2. 版本号规则

自定义镜像标签采用以下格式：

```text
<Dify 官方版本>-gizwits.<构建日期>.<当日修订号>
```

示例：

```text
1.16.1-gizwits.20260814.1
```

含义：

- `1.16.1`：基于 Dify 官方 `1.16.1` 版本。
- `gizwits`：Gizwits 定制版本。
- `20260814`：构建日期，格式为 `YYYYMMDD`。
- `.1`：当天第 1 个定制版本；同一天再次发布时依次使用 `.2`、`.3`。

官方 Dify 版本升级后，应同步更新标签中的官方版本号。例如：

```text
1.16.2-gizwits.20260820.1
1.17.0-gizwits.20260901.1
```

本次诊断镜像使用：

```text
iot-cn-beijing.cr.volces.com/gizwits_platform/dify-api:1.16.1-gizwits.20260814.1
```

## 3. 构建前准备

确保本地已安装并启动 Docker，并能够使用 `docker buildx`。

进入 Dify 源码根目录：

```bash
cd /Users/codemak1r/Documents/giz_dify
```

确认当前源码和待发布改动：

```bash
git status --short
git diff
```

登录火山引擎镜像仓库。如果本机已经登录，可以跳过：

```bash
docker login iot-cn-beijing.cr.volces.com
```

## 4. 构建并推送镜像

### 4.1 AMD64 集群

如果 VKE 节点架构为 x86_64/AMD64，执行：

```bash
docker buildx build --platform linux/amd64 -f api/Dockerfile -t iot-cn-beijing.cr.volces.com/gizwits_platform/dify-api:1.16.1-gizwits.20260814.1 --push .
```

### 4.2 ARM64 集群

如果 VKE 节点架构为 ARM64，执行：

```bash
docker buildx build --platform linux/arm64 -f api/Dockerfile -t iot-cn-beijing.cr.volces.com/gizwits_platform/dify-api:1.16.1-gizwits.20260814.1 --push .
```

构建命令最后的 `.` 表示使用 Dify 仓库根目录作为构建上下文，不能省略。`api/Dockerfile` 构建时还需要读取仓库中的 `dify-agent` 目录，因此不能进入 `api` 目录后执行构建。

如果 Docker 提示没有可用的 buildx builder，执行：

```bash
docker buildx create --name dify-builder --use
docker buildx inspect --bootstrap
```

然后重新执行构建命令。

## 5. 验证远程镜像

检查镜像是否已经推送成功，以及镜像平台是否与 VKE 节点一致：

```bash
docker buildx imagetools inspect iot-cn-beijing.cr.volces.com/gizwits_platform/dify-api:1.16.1-gizwits.20260814.1
```

AMD64 集群应包含：

```text
linux/amd64
```

ARM64 集群应包含：

```text
linux/arm64
```

## 6. 在 VKE 部署镜像

1. 登录火山引擎 VKE 控制台。
2. 进入 Dify 所在集群和 `dify-debug` 命名空间。
3. 打开实际接收 `/v1/audio-to-text` 请求的 API 负载。可通过以下请求日志确认目标负载：

   ```text
   POST /v1/audio-to-text 415
   ```

4. 编辑负载中的 API 容器。
5. 将原镜像：

   ```text
   docker.1ms.run/langgenius/dify-api:1.16.1
   ```

   替换为：

   ```text
   iot-cn-beijing.cr.volces.com/gizwits_platform/dify-api:1.16.1-gizwits.20260814.1
   ```

6. 保留现有 ConfigMap、Secret、端口、资源限制和探针设置。
7. 如果火山镜像仓库为私有仓库，在负载中选择具有拉取权限的镜像仓库凭证。
8. 保存配置并等待 Pod 重新创建。

镜像使用唯一标签时，可以保留 `IfNotPresent` 拉取策略。请勿重复覆盖并使用相同标签，以免节点继续使用缓存中的旧镜像。

当前负载如果采用 `Recreate` 更新策略，镜像切换期间可能出现短暂不可用。

## 7. 日志配置

为了记录 API 请求，可以在 API 负载中保留：

```yaml
- name: ENABLE_REQUEST_LOGGING
  value: 'true'
- name: LOG_LEVEL
  value: INFO
```

`INFO` 级别足以输出请求结果和本次新增的 MIME 诊断警告。除非需要更详细的请求调试信息，否则不建议长期使用 `DEBUG`，避免健康检查和框架日志过多。

## 8. 部署验证

### 8.1 检查 Pod 状态

在 VKE 控制台确认：

- Pod 状态为 `Running`。
- readiness/startup probe 已通过。
- 容器重启次数没有持续增加。
- `/health` 返回 HTTP 200。

### 8.2 验证 ASR 请求

从 App 重新发起录音转文字请求，在 API 容器日志中搜索：

```text
POST /v1/audio-to-text
```

当上传文件被 MIME 白名单拒绝时，应出现：

```text
Rejected ASR upload with unsupported audio type
```

日志会包含：

- `filename`
- `content_type`
- `mimetype`
- `allowed_mime_types`

日志不会打印音频内容、API Key 或用户凭证。

## 9. 回滚

如果新镜像无法启动或出现异常，在 VKE 负载中将镜像恢复为：

```text
docker.1ms.run/langgenius/dify-api:1.16.1
```

保存配置，等待 Pod 恢复并重新通过健康检查。

也可以回滚到上一个已经验证过的 Gizwits 镜像标签。不要删除已用于生产部署的历史镜像，以便快速回滚。

## 10. 后续发布

每次发布前：

1. 确认基于的 Dify 官方版本。
2. 生成新的日期和当日修订号标签。
3. 检查本地 Git 差异。
4. 构建并推送唯一标签的镜像。
5. 在 VKE 中替换镜像。
6. 验证健康检查和关键接口。
7. 记录部署的镜像标签和变更内容。
