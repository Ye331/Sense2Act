package com.sense2act.backend.domain.org;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

/**
 * org_stats:BaseMapper 负责读(画像),recompute 负责入库后的集合式重算。
 * 纯 SQL 重算:均值/样本标准差/P95/近 30 天日均频次,窗口 730 天。
 */
public interface OrgStatsMapper extends BaseMapper<OrgStat> {

    @Update("""
            INSERT INTO org_stats (org_id, category, window_days, sample_count,
                                   amount_mean, amount_std, amount_p95, freq_mean_30d, last_doc_at, updated_at)
            SELECT d.org_id, d.category, 730,
                   count(*),
                   avg(d.amount),
                   stddev_samp(d.amount),
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY d.amount),
                   round((count(*) FILTER (WHERE d.publish_date >= current_date - 30))::numeric / 30, 4),
                   max(d.publish_date),
                   now()
            FROM documents d
            WHERE d.org_id = #{orgId}
              AND d.category = #{category}
              AND d.publish_date >= current_date - 730
            GROUP BY d.org_id, d.category
            ON CONFLICT (org_id, category) DO UPDATE SET
                sample_count   = EXCLUDED.sample_count,
                amount_mean    = EXCLUDED.amount_mean,
                amount_std     = EXCLUDED.amount_std,
                amount_p95     = EXCLUDED.amount_p95,
                freq_mean_30d  = EXCLUDED.freq_mean_30d,
                last_doc_at    = EXCLUDED.last_doc_at,
                updated_at     = now()
            """)
    int recompute(@Param("orgId") String orgId, @Param("category") String category);
}
