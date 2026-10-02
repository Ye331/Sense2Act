package com.sense2act.backend.domain.org;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import com.sense2act.backend.common.BusinessException;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.document.DocumentService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.text.Normalizer;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * 机构名归一与解析:org_name 进来、org_id 出去(§9.1)。
 * 归一规则:NFKC(全角转半角)+ 空白折叠;解析顺序:规范名精确命中 → 别名包含 → 新建机构。
 */
@Service
public class OrgService {

    private static final Logger log = LoggerFactory.getLogger(OrgService.class);

    private static final int RECENT_DOCS = 10;

    private final OrganizationMapper orgMapper;
    private final OrgStatsMapper orgStatsMapper;
    private final DocumentService documentService;
    private final ObjectMapper objectMapper;

    public OrgService(OrganizationMapper orgMapper, OrgStatsMapper orgStatsMapper,
                      DocumentService documentService, ObjectMapper objectMapper) {
        this.orgMapper = orgMapper;
        this.orgStatsMapper = orgStatsMapper;
        this.documentService = documentService;
        this.objectMapper = objectMapper;
    }

    /** E1-5 机构画像:基本信息 + org_stats + 近期文档。 */
    @org.springframework.transaction.annotation.Transactional(readOnly = true)
    public OrgDetailView detail(String id) {
        Organization org = orgMapper.selectById(id);
        if (org == null) {
            throw BusinessException.notFound("机构不存在: " + id);
        }
        List<OrgStatView> stats = orgStatsMapper.selectList(new LambdaQueryWrapper<OrgStat>()
                        .eq(OrgStat::getOrgId, id).orderByAsc(OrgStat::getCategory))
                .stream()
                .map(s -> new OrgStatView(s.getCategory(), s.getWindowDays(), s.getSampleCount(),
                        s.getAmountMean(), s.getAmountStd(), s.getAmountP95(),
                        s.getFreqMean30d(), s.getLastDocAt()))
                .toList();
        return new OrgDetailView(org.getId(), org.getName(), org.getAliases(), org.getType(),
                org.getUscc(), org.getRegion(), org.getCreatedAt(), stats,
                documentService.recentByOrg(id, RECENT_DOCS));
    }

    /** 机构画像响应:金额类按约定序列化为字符串小数。 */
    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record OrgDetailView(String id, String name, List<String> aliases, String type, String uscc,
                                String region, OffsetDateTime createdAt, List<OrgStatView> stats,
                                List<DocumentService.DocView> recentDocuments) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    public record OrgStatView(String category, Integer windowDays, Integer sampleCount,
                              @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal amountMean,
                              @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal amountStd,
                              @JsonFormat(shape = JsonFormat.Shape.STRING) BigDecimal amountP95,
                              BigDecimal freqMean30d, LocalDate lastDocAt) {
    }

    public static String normalizeName(String raw) {
        if (raw == null) {
            return null;
        }
        String s = Normalizer.normalize(raw, Normalizer.Form.NFKC);
        s = s.replaceAll("\\s+", " ").trim();
        return s.isEmpty() ? null : s;
    }

    /** 解析机构 ID;org_name 为空返回 null(文档可不挂机构)。 */
    public String resolveOrgId(String orgName) {
        String name = normalizeName(orgName);
        if (name == null) {
            return null;
        }
        if (name.length() > 200) {
            name = name.substring(0, 200);
        }
        Organization exact = orgMapper.selectOne(
                new LambdaQueryWrapper<Organization>().eq(Organization::getName, name).last("LIMIT 1"));
        if (exact != null) {
            return exact.getId();
        }
        Organization byAlias = orgMapper.selectOne(
                new LambdaQueryWrapper<Organization>().apply("aliases @> {0}::jsonb", toJson(List.of(name)))
                        .last("LIMIT 1"));
        if (byAlias != null) {
            return byAlias.getId();
        }
        Organization org = new Organization();
        org.setId(IdGen.next("org"));
        org.setName(name);
        org.setAliases(List.of());
        orgMapper.insert(org);
        log.info("新建机构 {} = {}", org.getId(), name);
        return org.getId();
    }

    private String toJson(List<String> value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("别名序列化失败", e);
        }
    }
}
