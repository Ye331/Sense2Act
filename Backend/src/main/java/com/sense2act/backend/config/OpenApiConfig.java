package com.sense2act.backend.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import io.swagger.v3.oas.models.Components;
import io.swagger.v3.oas.models.security.SecurityScheme;
import io.swagger.v3.oas.models.security.SecurityRequirement;
import org.springdoc.core.customizers.OpenApiCustomizer;
import java.util.List;

/** OpenAPI 元信息。契约以 docs/api-design.md 为准,swagger 只做只读镜像(DoD)。 */
@Configuration
public class OpenApiConfig {

    @Bean
    OpenAPI openAPI() {
        return new OpenAPI()
                .components(new Components()
                        .addSecuritySchemes("bearerAuth", new SecurityScheme().type(SecurityScheme.Type.HTTP)
                                .scheme("bearer").bearerFormat("JWT"))
                        .addSecuritySchemes("internalKey", new SecurityScheme().type(SecurityScheme.Type.APIKEY)
                                .in(SecurityScheme.In.HEADER).name("X-Internal-Key")))
                .addSecurityItem(new SecurityRequirement().addList("bearerAuth"))
                .info(new Info()
                        .title("Sense2Act Backend API")
                        .description("招投标商机发现后端。契约文档:docs/api-design.md;"
                                + "用户接口经 JWT，内部接口(§9)经 X-Internal-Key 认证。")
                        .version("v0.1"));
    }

    @Bean
    OpenApiCustomizer endpointAuthentication() {
        return api -> api.getPaths().forEach((path, item) -> item.readOperationsMap().forEach((method, op) -> {
            if (path.equals("/api/v1/auth/token")) {
                op.setSecurity(List.of());
            } else if (path.startsWith("/api/v1/internal/")) {
                op.setSecurity(List.of(new SecurityRequirement().addList("internalKey")));
            } else if (method == io.swagger.v3.oas.models.PathItem.HttpMethod.GET
                    && (path.startsWith("/api/v1/documents") || path.startsWith("/api/v1/organizations"))) {
                op.setSecurity(List.of(new SecurityRequirement().addList("bearerAuth"),
                        new SecurityRequirement().addList("internalKey")));
            }
        }));
    }
}
