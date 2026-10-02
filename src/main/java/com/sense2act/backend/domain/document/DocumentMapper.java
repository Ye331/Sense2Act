package com.sense2act.backend.domain.document;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Param;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 文档 Mapper。检索类语句在 resources/mapper/DocumentMapper.xml(动态 SQL,过滤片段复用)。
 * 返回列不含 raw/embedding:raw 走 selectById(autoResultMap 的 typeHandler),embedding 不进实体(D12)。
 */
public interface DocumentMapper extends BaseMapper<Document> {

    /** 关键词/无条件列表检索(ILIKE 子串匹配,支持中文;排序白名单已在上层校验)。 */
    List<Document> searchKeyword(@Param("patterns") List<String> patterns,
                                 @Param("docType") String docType, @Param("orgId") String orgId,
                                 @Param("region") String region, @Param("category") String category,
                                 @Param("amountGte") BigDecimal amountGte, @Param("amountLte") BigDecimal amountLte,
                                 @Param("dateFrom") LocalDate dateFrom, @Param("dateTo") LocalDate dateTo,
                                 @Param("sort") String sort,
                                 @Param("limit") int limit, @Param("offset") long offset);

    long countKeyword(@Param("patterns") List<String> patterns,
                      @Param("docType") String docType, @Param("orgId") String orgId,
                      @Param("region") String region, @Param("category") String category,
                      @Param("amountGte") BigDecimal amountGte, @Param("amountLte") BigDecimal amountLte,
                      @Param("dateFrom") LocalDate dateFrom, @Param("dateTo") LocalDate dateTo);

    /** 纯语义检索:cosine 距离升序;无 embedding 的文档天然不在结果中。 */
    List<Document> searchSemantic(@Param("queryVector") String queryVector,
                                  @Param("docType") String docType, @Param("orgId") String orgId,
                                  @Param("region") String region, @Param("category") String category,
                                  @Param("amountGte") BigDecimal amountGte, @Param("amountLte") BigDecimal amountLte,
                                  @Param("dateFrom") LocalDate dateFrom, @Param("dateTo") LocalDate dateTo,
                                  @Param("limit") int limit, @Param("offset") long offset);

    long countSemantic(@Param("docType") String docType, @Param("orgId") String orgId,
                       @Param("region") String region, @Param("category") String category,
                       @Param("amountGte") BigDecimal amountGte, @Param("amountLte") BigDecimal amountLte,
                       @Param("dateFrom") LocalDate dateFrom, @Param("dateTo") LocalDate dateTo);

    /** keyword + semantic 同给:RRF 融合两路排名,score = Σ 1/(60 + rank),纯 SQL。 */
    List<Document> searchRrf(@Param("patterns") List<String> patterns,
                             @Param("queryVector") String queryVector,
                             @Param("docType") String docType, @Param("orgId") String orgId,
                             @Param("region") String region, @Param("category") String category,
                             @Param("amountGte") BigDecimal amountGte, @Param("amountLte") BigDecimal amountLte,
                             @Param("dateFrom") LocalDate dateFrom, @Param("dateTo") LocalDate dateTo,
                             @Param("limit") int limit, @Param("offset") long offset);

    long countRrf(@Param("patterns") List<String> patterns,
                  @Param("docType") String docType, @Param("orgId") String orgId,
                  @Param("region") String region, @Param("category") String category,
                  @Param("amountGte") BigDecimal amountGte, @Param("amountLte") BigDecimal amountLte,
                  @Param("dateFrom") LocalDate dateFrom, @Param("dateTo") LocalDate dateTo);

    /** D12:embedding 补算写入(vector 字面量如 "[0.1,0.2,...]",列 vector(512))。 */
    int updateEmbedding(@Param("id") String id, @Param("vector") String vectorLiteral);

    /** 按给定 id 取尚无 embedding 的文档(补算跳过已算过的)。 */
    List<Document> selectWithoutEmbedding(@Param("ids") List<String> ids);
}
