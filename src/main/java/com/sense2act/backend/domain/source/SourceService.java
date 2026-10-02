package com.sense2act.backend.domain.source;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.common.events.Events;
import com.sense2act.backend.common.events.SourceDegradedEvent;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.ingesterror.IngestError;
import com.sense2act.backend.domain.ingesterror.IngestErrorMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.scheduling.support.CronExpression;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.List;
import java.util.Set;

/**
 * 信息源:CRUD、到期判定(D2)、运行回报与降级(D3)。
 * 到期判定只按 schedule_cron + last_run_at,在后端完成(§9.1);领取无副作用不加锁,重复领取无害。
 */
@Service
public class SourceService {

    private static final Logger log = LoggerFactory.getLogger(SourceService.class);

    private static final Set<String> TYPES = Set.of("web_page", "api", "rss");
    private static final Set<String> STAGES = Set.of("fetch", "normalize", "dedup", "persist");
    private static final int DEGRADED_AFTER = 3;   // D3:连续 3 次失败 → degraded
    private static final int DOWN_AFTER = 10;      // D3:连续 10 次失败 → down

    private final SourceMapper sourceMapper;
    private final DocumentMapper documentMapper;
    private final IngestErrorMapper errorMapper;
    private final ApplicationEventPublisher publisher;

    public SourceService(SourceMapper sourceMapper, DocumentMapper documentMapper,
                         IngestErrorMapper errorMapper, ApplicationEventPublisher publisher) {
        this.sourceMapper = sourceMapper;
        this.documentMapper = documentMapper;
        this.errorMapper = errorMapper;
        this.publisher = publisher;
    }

    @Transactional(readOnly = true)
    public PageResult<Source> list(PageParams params) {
        Page<Source> page = sourceMapper.selectPage(
                new Page<>(params.page(), params.pageSize()),
                new LambdaQueryWrapper<Source>().orderByAsc(Source::getCreatedAt));
        return PageResult.of(page.getRecords(), page.getTotal(), params);
    }

    @Transactional
    public Source create(SourceReq req) {
        if (isBlank(req.name())) throw BusinessException.badRequest("name 必填");
        if (isBlank(req.url())) throw BusinessException.badRequest("url 必填");
        if (!TYPES.contains(req.type())) throw BusinessException.badRequest("type 必须是 web_page/api/rss 之一");
        validateCron(req.scheduleCron());

        Source s = new Source();
        s.setId(IdGen.next("src"));
        s.setName(req.name().trim());
        s.setType(req.type());
        s.setUrl(req.url().trim());
        s.setAdapter(blankToNull(req.adapter()));
        s.setScheduleCron(blankToNull(req.scheduleCron()));
        s.setConfig(req.config() == null ? new java.util.LinkedHashMap<>() : req.config());
        s.setEnabled(req.enabled() == null || req.enabled());
        s.setHealth("ok");
        s.setConsecutiveFailures(0);
        sourceMapper.insert(s);
        return s;
    }

    @Transactional
    public Source update(String id, SourceReq req) {
        Source s = mustGet(id);
        if (req.name() != null) {
            if (isBlank(req.name())) throw BusinessException.badRequest("name 不能为空");
            s.setName(req.name().trim());
        }
        if (req.url() != null) {
            if (isBlank(req.url())) throw BusinessException.badRequest("url 不能为空");
            s.setUrl(req.url().trim());
        }
        if (req.type() != null) {
            if (!TYPES.contains(req.type())) throw BusinessException.badRequest("type 必须是 web_page/api/rss 之一");
            s.setType(req.type());
        }
        if (req.adapter() != null) s.setAdapter(blankToNull(req.adapter()));
        if (req.scheduleCron() != null) {
            validateCron(req.scheduleCron());
            s.setScheduleCron(blankToNull(req.scheduleCron()));
        }
        if (req.config() != null) s.setConfig(req.config());
        if (req.enabled() != null) s.setEnabled(req.enabled());
        s.setUpdatedAt(OffsetDateTime.now());
        sourceMapper.updateById(s);   // null 字段不更新,PATCH 天然是部分更新
        return s;
    }

    @Transactional
    public void delete(String id) {
        mustGet(id);
        long docs = documentMapper.selectCount(
                new LambdaQueryWrapper<Document>().eq(Document::getSourceId, id));
        if (docs > 0) {
            throw BusinessException.conflict("该源下已有 " + docs + " 篇文档,不能删除;如需停用可 PATCH enabled=false");
        }
        sourceMapper.deleteById(id);
    }

    /** D2:手动触发 = 置为"从未跑过",下轮 queue 立即可领。真实 last_run_at 由 ingest-runs 回报写入。 */
    @Transactional
    public Source triggerRun(String id) {
        Source s = mustGet(id);
        sourceMapper.update(null, new LambdaUpdateWrapper<Source>()
                .eq(Source::getId, id)
                .set(Source::getLastRunAt, null)
                .set(Source::getUpdatedAt, OffsetDateTime.now()));
        log.info("源 {} 手动触发采集(D2:置为到期)", id);
        return sourceMapper.selectById(id);
    }

    /** 到期源列表(仅 enabled)。判定只看 schedule_cron + last_run_at,无副作用。 */
    @Transactional(readOnly = true)
    public List<Source> dueSources() {
        return sourceMapper.selectList(new LambdaQueryWrapper<Source>().eq(Source::getEnabled, true))
                .stream().filter(this::isDue).toList();
    }

    boolean isDue(Source s) {
        if (s.getLastRunAt() == null) {
            return true;                      // 从未跑过 → 立即到期
        }
        String cron = s.getScheduleCron();
        if (cron == null || cron.isBlank()) {
            return false;                     // 无 cron = 仅手动触发
        }
        try {
            ZonedDateTime lastRun = s.getLastRunAt().atZoneSameInstant(ZoneId.systemDefault());
            ZonedDateTime next = CronExpression.parse(cron.trim()).next(lastRun);
            return next != null && next.isBefore(ZonedDateTime.now());
        } catch (IllegalArgumentException e) {
            return false;                     // 坏 cron 不自动到期(入口已校验,此处兜底)
        }
    }

    /** E1-4:上报一次运行。D3:连续 3 次失败 degraded、10 次 down、成功即恢复 ok。 */
    @Transactional
    public Source recordRun(String sourceId, boolean ok, List<RunError> errors) {
        Source s = mustGet(sourceId);
        // stage 先整批校验:任一非法整个请求 40001,不留半截写入
        for (RunError e : errors) {
            if (!STAGES.contains(e.stage())) {
                throw BusinessException.badRequest("stage 必须是 fetch/normalize/dedup/persist 之一: " + e.stage());
            }
        }
        OffsetDateTime now = OffsetDateTime.now();
        int failures = ok ? 0 : (s.getConsecutiveFailures() == null ? 1 : s.getConsecutiveFailures() + 1);
        String oldHealth = s.getHealth() == null ? "ok" : s.getHealth();
        String newHealth = ok ? "ok"
                : failures >= DOWN_AFTER ? "down"
                : failures >= DEGRADED_AFTER ? "degraded"
                : oldHealth;

        sourceMapper.update(null, new LambdaUpdateWrapper<Source>()
                .eq(Source::getId, sourceId)
                .set(Source::getLastRunAt, now)
                .set(Source::getLastRunStatus, ok ? "ok" : "failed")
                .set(Source::getConsecutiveFailures, failures)
                .set(Source::getHealth, newHealth)
                .set(Source::getUpdatedAt, now));

        for (RunError e : errors) {
            IngestError err = new IngestError();
            err.setId(IdGen.next("ier"));
            err.setSourceId(sourceId);
            err.setUrl(e.url());
            err.setStage(e.stage());
            err.setError(e.error());
            err.setOccurredAt(now);
            errorMapper.insert(err);
        }
        if (!newHealth.equals(oldHealth) && ("degraded".equals(newHealth) || "down".equals(newHealth))) {
            // 事件流本身 E2-7 落地,此处只发事件
            Events.publishAfterCommit(publisher,
                    new SourceDegradedEvent(sourceId, s.getName(), newHealth, Instant.now()));
        }
        return sourceMapper.selectById(sourceId);
    }

    private Source mustGet(String id) {
        Source s = sourceMapper.selectById(id);
        if (s == null) {
            throw BusinessException.notFound("信息源不存在: " + id);
        }
        return s;
    }

    private static void validateCron(String cron) {
        if (cron == null || cron.isBlank()) {
            return;
        }
        try {
            CronExpression.parse(cron.trim());
        } catch (IllegalArgumentException e) {
            throw BusinessException.badRequest("schedule_cron 非法(Spring 六段式): " + cron);
        }
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.trim();
    }

    /** 源创建/部分更新请求。PATCH 语义:字段为 null 表示不修改。 */
    @com.fasterxml.jackson.databind.annotation.JsonNaming(
            com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record SourceReq(String name, String type, String url, String adapter,
                            String scheduleCron, java.util.Map<String, Object> config, Boolean enabled) {
    }

    /** ingest-runs 上报的单条错误。 */
    public record RunError(String url, String stage, String error) {
    }
}
