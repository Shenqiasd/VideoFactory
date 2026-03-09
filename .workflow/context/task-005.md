# task-005: 创建 SocialAutoUploadAdapter

创建 SocialAutoUploadAdapter 适配器文件 [REMEMBER] 项目使用 social-auto-upload 进行多平台发布，支持抖音/B站/小红书/YouTube/视频号 5 个平台 [DECISION] 将适配器逻辑从 publisher.py 分离到独立的 adapter.py 文件，提高代码模块化 [ARCHITECTURE] 适配器模式：基类 SocialAutoUploadAdapter 定义接口，LocalSocialAutoUploadAdapter 本地执行脚本，RemoteSocialAutoUploadAdapter 通过 VPS 网关远程执行
