package com.sense2act.backend.domain.dashboard;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import com.sense2act.backend.domain.investigation.Investigation;
import com.sense2act.backend.domain.investigation.InvestigationMapper;
import com.sense2act.backend.domain.report.Report;
import com.sense2act.backend.domain.report.ReportMapper;
import com.sense2act.backend.domain.signal.Signal;
import com.sense2act.backend.domain.signal.SignalMapper;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 看板总览(E5-2,契约 §2 看板组):四类对象计数 + 当日增量。
 * by_status 固定给出全部状态键(缺的补 0),前端不必处理缺键。
 */
@Service
public class DashboardService {

    private static final List<String> SIGNAL_STATUSES = List.of("pending", "investigating", "confirmed", "dismissed");
    private static final List<String> INVESTIGATION_STATUSES =
            List.of("created", "investigating", "reporting", "completed", "failed", "stopped");
    private static final List<String> REPORT_STATUSES = List.of("draft", "published");

    private final DocumentMapper documentMapper;
    private final SignalMapper signalMapper;
    private final InvestigationMapper investigationMapper;
    private final ReportMapper reportMapper;

    public DashboardService(DocumentMapper documentMapper, SignalMapper signalMapper,
                            InvestigationMapper investigationMapper, ReportMapper reportMapper) {
        this.documentMapper = documentMapper;
        this.signalMapper = signalMapper;
        this.investigationMapper = investigationMapper;
        this.reportMapper = reportMapper;
    }

    public Map<String, Object> summary() {
        OffsetDateTime todayStart = LocalDate.now(ZoneId.systemDefault()).atStartOfDay()
                .atOffset(OffsetDateTime.now().getOffset());

        Map<String, Object> documents = new LinkedHashMap<>();
        documents.put("total", documentMapper.selectCount(null));
        documents.put("today", documentMapper.selectCount(
                new LambdaQueryWrapper<Document>().ge(Document::getCreatedAt, todayStart)));

        Map<String, Object> signals = new LinkedHashMap<>();
        signals.put("total", signalMapper.selectCount(null));
        signals.put("today", signalMapper.selectCount(
                new LambdaQueryWrapper<Signal>().ge(Signal::getCreatedAt, todayStart)));
        signals.put("by_status", countByStatus(signalMapper, Signal::getStatus, SIGNAL_STATUSES));

        Map<String, Object> investigations = new LinkedHashMap<>();
        investigations.put("total", investigationMapper.selectCount(null));
        investigations.put("today", investigationMapper.selectCount(
                new LambdaQueryWrapper<Investigation>().ge(Investigation::getCreatedAt, todayStart)));
        investigations.put("by_status", countByStatus(investigationMapper, Investigation::getStatus,
                INVESTIGATION_STATUSES));

        Map<String, Object> reports = new LinkedHashMap<>();
        reports.put("total", reportMapper.selectCount(null));
        reports.put("today", reportMapper.selectCount(
                new LambdaQueryWrapper<Report>().ge(Report::getCreatedAt, todayStart)));
        reports.put("by_status", countByStatus(reportMapper, Report::getStatus, REPORT_STATUSES));

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("documents", documents);
        data.put("signals", signals);
        data.put("investigations", investigations);
        data.put("reports", reports);
        return data;
    }

    /** by_status 计数,固定给出全部状态键(缺的补 0);selectCount 不带 ORDER BY(坑位)。 */
    private <T> Map<String, Long> countByStatus(com.baomidou.mybatisplus.core.mapper.BaseMapper<T> mapper,
                                                com.baomidou.mybatisplus.core.toolkit.support.SFunction<T, ?> statusCol,
                                                List<String> statuses) {
        Map<String, Long> byStatus = new LinkedHashMap<>();
        for (String status : statuses) {
            byStatus.put(status, mapper.selectCount(new LambdaQueryWrapper<T>().eq(statusCol, status)));
        }
        return byStatus;
    }
}
