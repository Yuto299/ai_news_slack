from bot.channels import ai_news, launches, pain_points, small_biz, subsidies, weekly_trends

CHANNELS = {
    m.NAME: m for m in [ai_news, small_biz, pain_points, launches, weekly_trends, subsidies]
}
