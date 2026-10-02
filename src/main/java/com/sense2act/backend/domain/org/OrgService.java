package com.sense2act.backend.domain.org;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.IdGen;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.text.Normalizer;
import java.util.List;

/**
 * 机构名归一与解析:org_name 进来、org_id 出去(§9.1)。
 * 归一规则:NFKC(全角转半角)+ 空白折叠;解析顺序:规范名精确命中 → 别名包含 → 新建机构。
 */
@Service
public class OrgService {

    private static final Logger log = LoggerFactory.getLogger(OrgService.class);

    private final OrganizationMapper orgMapper;
    private final ObjectMapper objectMapper;

    public OrgService(OrganizationMapper orgMapper, ObjectMapper objectMapper) {
        this.orgMapper = orgMapper;
        this.objectMapper = objectMapper;
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
