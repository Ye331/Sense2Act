package com.sense2act.backend.domain.embedding;

import com.sense2act.backend.config.AppProperties;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.List;
import java.util.Map;

/**
 * D12 唯一的出站数据面调用:POST EMBEDDING_ENDPOINT,body {"inputs":["文本",...]},
 * 响应 {"embeddings":[[512 个 float],...]}。可选 Bearer 鉴权(EMBEDDING_API_KEY)。
 * 一次调用无状态无缓存,性质等同查数据库;失败由调用方降级,不向上抛。
 */
@Component
public class EmbeddingClient {

    /** 文本与查询向量同源同维度,与 documents.embedding vector(512) 对齐。 */
    public static final int VECTOR_DIM = 512;

    private final AppProperties.Embedding props;
    private final RestClient rest;

    public EmbeddingClient(AppProperties props) {
        this.props = props.embedding();
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        int timeoutMs = (int) this.props.timeout().toMillis();
        factory.setConnectTimeout(timeoutMs);
        factory.setReadTimeout(timeoutMs);
        this.rest = RestClient.builder()
                .baseUrl(this.props.endpoint())
                .requestFactory(factory)
                .build();
    }

    /** 批量把文本换成向量;数量/维度不符抛 IllegalStateException,由调用方决定降级。 */
    public List<float[]> embed(List<String> inputs) {
        EmbeddingsResponse resp = rest.post()
                .contentType(MediaType.APPLICATION_JSON)
                .headers(h -> {
                    String key = props.apiKey();
                    if (key != null && !key.isBlank()) {
                        h.setBearerAuth(key);
                    }
                })
                .body(Map.of("inputs", inputs))
                .retrieve()
                .body(EmbeddingsResponse.class);
        if (resp == null || resp.embeddings() == null || resp.embeddings().length != inputs.size()) {
            throw new IllegalStateException("embedding 端点返回数量与输入不符");
        }
        return List.of(resp.embeddings());
    }

    record EmbeddingsResponse(float[][] embeddings) {
    }
}
