// 提交到 GitHub 的模板，apiKey 必须留空。
// 真实 key 写在本目录的 config.local.js（已在 .gitignore 里），
// 或设环境变量 SHANGHAITECH_API_KEY，两者任一存在即可运行。
const SMART_NOTES_CONFIG = {
    // 上海科技大学 GenAI 网关，末尾到 /api/v1/start，不要再拼 /v1
    apiBaseUrl: "https://genaiapi.shanghaitech.edu.cn/api/v1/start",
    apiKey: "",
    // 网关实际只有一个后端，写哪个 id 都会路由到 deepseek-pro
    model: "deepseek-v4-pro"
};
