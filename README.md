# FamilyNet

管理机场订阅与自有 VPS 节点的代理客户端。

[产品与下载页面](https://z-q-chen.github.io/familynet/) · [0.3.1 安装包与完整对应源码](https://github.com/z-q-chen/FamilyNet/releases/tag/v0.3.1)

## 当前版本

- Ubuntu 26.04 x64：已构建并完成本机界面和回环链路验证；系统代理/TUN 实际接管未验收。
- Windows x64：便携 ZIP，未签名、未实机验收。
- Android arm64：开发签名测试 APK，最低 API 24；签名、原生库与 16KB 对齐已检验，手机 VPN、后台保活和真实节点仍待实机验收。
- macOS、iOS/iPadOS：暂无完成交付的自有安装包。

支持 Clash/Mihomo 配置、Base64、标准节点链接的本地导入，配置更新、节点组与分流、连接日志和本地加密备份。VPS/CDN 工具提供 REALITY、Hysteria2 与 Trojan/WebSocket/TLS 配置和部署回滚；没有公网部署验收。

本程序不提供机场账号或节点。订阅、节点、DNS 与规则资源按照用户自己的配置联网；辅助自动更新、IP 查询和云备份关闭。

## 源码与许可

这是修改后的开源客户端发行。桌面基于 Clash Party v2.0.4 与 Mihomo v1.19.33，移动端基于 FlClash v0.9.2。保留上游 GPL-3.0、版权和第三方许可；独立控制平面使用 MIT。

**完整对应源码在 Release 附件 `FamilyNet-0.3.1-source.zip` 中**，包含应用源码、修改、构建脚本、锁定资源、Mihomo 源码和第三方许可。GitHub 自动生成的 Source code ZIP 只包含这个发行索引仓库，不能代替完整源码附件。

所有安装包和源码包的 SHA256 见 Release 的 `SHA256SUMS`。本仓库与发行包不包含用户订阅、运行数据或签名私钥。
