"""解释只使用实际记录，不补造正文或历史。"""
import math


def format_amount(value):
    return '未披露' if value is None or not math.isfinite(float(value)) else f'{float(value):,.2f} 元'


def build_explanation(record: dict) -> list[str]:
    reasons = [f"历史截止日：{record['history_cutoff']:%Y-%m-%d}；只使用真实采购历史。"]
    deadline = record.get('deadline')
    date_text = '未知' if deadline is None or str(deadline) == 'NaT' else f'{deadline:%Y-%m-%d}'
    reasons.append(f"北京时间评估日期：{record['evaluation_date']:%Y-%m-%d}；投标截止日期：{date_text}；{record['deadline_reason']}。")
    if record.get('content_source_note'):
        reasons.append(record['content_source_note'])
    if record.get('is_project_update'):
        reasons.append(f"项目更新，归属 {record['procurement_project_id']}，首次公告 {record['first_announcement_id']}；{record['project_match_method']}。不重复评分或提交。")
        return reasons
    reasons.append(f"企业相关性 {record['relevance_score']:.3f}，准入门槛 {record['relevance_threshold']:.3f}。")
    if not record['relevance_pass']:
        reasons.append('未通过相关性筛选，保留公告。')
        return reasons
    if record['amount_reliable']:
        reasons.append(f"金额 {format_amount(record['amount_yuan'])}，历史 {int(record['amount_history_count'])} 条有效金额中位数 {format_amount(record['historical_median_amount'])}，比值 {record['amount_ratio']:.2f}，金额得分 {record['amount_score']:.3f}。")
    else:
        reasons.append(f"金额不可用：{record['amount_status']}；有效金额历史 {int(record['amount_history_count'])} 条。")
    if record['novelty_reliable']:
        reasons.append(f"新颖性历史 {int(record['novelty_history_count'])} 个项目；最相似项目“{record['most_similar_history_project']}”（{record['most_similar_history_id']}），最大相似度 {record['max_history_similarity']:.3f}，新颖性得分 {record['novelty_score']:.3f}。")
        if record.get('most_similar_history_url'):
            reasons.append(f"对照项目：{record['most_similar_history_url']}")
    else:
        reasons.append(f"新颖性不可用：同单位语义历史 {int(record['novelty_history_count'])} 条，要求至少 {int(record['min_history_count'])} 条。")
    if record['frequency_reliable']:
        reasons.append(f"频率窗口 {int(record['frequency_window'])} 天，近期项目 {int(record['recent_count'])} 个，完整历史窗口 {int(record['frequency_history_windows_used'])} 个，历史平均 {record['historical_frequency']:.2f} 个，比值 {record['frequency_ratio']:.2f}，频率得分 {record['frequency_score']:.3f}。")
    else:
        reasons.append(f"频率不可用：统计窗口 {int(record['frequency_window'])} 天，近期项目 {int(record['recent_count'])} 个，完整历史窗口 {int(record['frequency_history_windows_used'])} 个；历史样本不足或完整窗口均值不可用/为零。")
    reasons.append(f"配置权重：相关性 {record['relevance_weight']:.0%}、金额 {record['amount_weight']:.0%}、新颖性 {record['novelty_weight']:.0%}、频率 {record['frequency_weight']:.0%}；不可用指标保留空值，按可用指标权重之和 {record['scoring_weight_sum']:.0%} 归一计分。")
    if record['priority_eligible'] and record['evidence_status'] != 'sufficient':
        reasons.append('历史指标依据不完整，仍参与统一排名；综合分不代表历史异常已获充分验证。')
    if record['priority_eligible']:
        reasons.append(f"综合分 {record['priority_score']:.3f}，当日排名 {int(record['rank'])}；{'进入' if record['is_signal'] else '未进入'}当日 Top-{int(record['top_k'])}。入选表示需要进一步核实，不代表已确认异常。")
    elif record['deadline_status'] == 'expired':
        reasons.append('公告已过截止日期，保留检测分数，不参与 Top-K，不自动提交。')
    else:
        reasons.append('参与排名的指标依据不足，保留检测结果，不自动提交。')
    return reasons
