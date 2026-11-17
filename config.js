// 提交到 GitHub 的模板。apiKey 必须留空，apiBaseUrl 留给使用者自己填。
// 真实配置写在同目录的 config.local.js（已在 .gitignore 里，不会提交），
// 或设环境变量 SMART_NOTES_API_KEY / SMART_NOTES_API_BASE，两者任一存在即可运行。
// 也可以直接用环境变量，见 README 的「配置」一节。
const SMART_NOTES_CONFIG = {
    // 填写你的 OpenAI 兼容接口地址，通常以 /v1 结尾。
    // 示例：https://api.deepseek.com/v1
    apiBaseUrl: "",
    apiKey: "",
    // 模型 id，按你的服务商填写
    model: "deepseek-chat"
};
