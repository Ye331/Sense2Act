package com.sense2act.backend.domain.embedding;

import com.sense2act.backend.config.AppProperties;
import com.sense2act.backend.domain.document.Document;
import com.sense2act.backend.domain.document.DocumentMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Optional;

/**
 * D12:文档向量入库后补算、查询向量实时算,同一端点保证同模型。
 * 端点未配置或调用失败 → 一律降级(补算放弃、查询走关键词),不报错不阻塞主流程。
 */
@Service
public class EmbeddingService {

    private static final Logger log = LoggerFactory.getLogger(EmbeddingService.class);

    private static final int MAX_TEXT_CHARS = 8000;   // 防超长文本撑爆端点
    private static final int BATCH = 32;

    private final AppProperties.Embedding props;
    private final EmbeddingClient client;
    private final DocumentMapper documentMapper;

    public EmbeddingService(AppProperties props, EmbeddingClient client, DocumentMapper documentMapper) {
        this.props = props.embedding();
        this.client = client;
        this.documentMapper = documentMapper;
    }

    public boolean enabled() {
        return props.endpoint() != null && !props.endpoint().isBlank();
    }

    /** 查询文本 → 向量。端点不可用/未配置/维度不符 → empty,调用方关键词兜底。 */
    public Optional<float[]> embedQuery(String text) {
        if (!enabled() || text == null || text.isBlank()) {
            return Optional.empty();
        }
        try {
            float[] vector = client.embed(List.of(text)).get(0);
            if (vector.length != EmbeddingClient.VECTOR_DIM) {
                throw new IllegalStateException("embedding 维度不符: " + vector.length);
            }
            return Optional.of(vector);
        } catch (Exception e) {
            log.warn("查询向量计算失败,语义检索降级为关键词: {}", e.getMessage());
            return Optional.empty();
        }
    }

    /**
     * 入库后补算文档向量(标题+正文,只补 embedding 为空的)。
     * 尽力而为:任何失败只记日志,文档照常可查(只是不进语义结果)。
     */
    public void backfill(List<String> docIds) {
        if (!enabled() || docIds == null || docIds.isEmpty()) {
            return;
        }
        try {
            List<Document> todo = documentMapper.selectWithoutEmbedding(docIds);
            if (todo.isEmpty()) {
                return;
            }
            for (int i = 0; i < todo.size(); i += BATCH) {
                List<Document> chunk = todo.subList(i, Math.min(i + BATCH, todo.size()));
                List<float[]> vectors = client.embed(chunk.stream().map(EmbeddingService::embedText).toList());
                for (int j = 0; j < chunk.size(); j++) {
                    float[] v = vectors.get(j);
                    if (v.length != EmbeddingClient.VECTOR_DIM) {
                        throw new IllegalStateException("embedding 维度不符: " + v.length);
                    }
                    documentMapper.updateEmbedding(chunk.get(j).getId(), toVectorLiteral(v));
                }
            }
            log.info("embedding 补算完成 {} 篇", todo.size());
        } catch (Exception e) {
            log.warn("embedding 补算失败(文档照常可查,仅不进语义结果): {}", e.getMessage());
        }
    }

    private static String embedText(Document d) {
        String text = (d.getTitle() == null ? "" : d.getTitle()) + "\n"
                + (d.getContentText() == null ? "" : d.getContentText());
        return text.length() > MAX_TEXT_CHARS ? text.substring(0, MAX_TEXT_CHARS) : text;
    }

    /** float[] → pgvector 字面量 "[0.1,0.2,...]"。 */
    public static String toVectorLiteral(float[] vector) {
        StringBuilder sb = new StringBuilder(vector.length * 10).append('[');
        for (int i = 0; i < vector.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(vector[i]);
        }
        return sb.append(']').toString();
    }
}
