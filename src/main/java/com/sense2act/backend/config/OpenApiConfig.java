package com.sense2act.backend.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** OpenAPI 元信息。契约以 docs/api-design.md 为准,swagger 只做只读镜像(DoD)。 */
@Configuration
public class OpenApiConfig {

    @Bean
    OpenAPI openAPI() {
        return new OpenAPI()
                .info(new Info()
                        .title("Sense2Act Backend API")
                        .description("招投标商机发现后端。契约文档:docs/api-design.md;"
                                + "内部接口(§9)经 X-Internal-Key 认证,不在本 swagger 暴露。")
                        .version("v0.1"));
    }
}
