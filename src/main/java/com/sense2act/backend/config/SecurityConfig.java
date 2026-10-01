package com.sense2act.backend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.ErrorCode;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.DelegatingPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.crypto.password.Pbkdf2PasswordEncoder;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

import java.io.IOException;
import java.util.List;
import java.util.Map;

/**
 * 安全配置:用户侧走 JWT(HS256),内部接口走 X-Internal-Key 门卫过滤器;
 * viewer 只读按 HTTP 方法统一封写操作(POST/PUT/PATCH/DELETE 需 admin/analyst)。
 */
@Configuration
@EnableMethodSecurity
public class SecurityConfig {

    @Bean
    SecurityFilterChain filterChain(HttpSecurity http, AppProperties props, ObjectMapper objectMapper)
            throws Exception {
        http.csrf(csrf -> csrf.disable())
                .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .exceptionHandling(eh -> eh
                        .authenticationEntryPoint((req, resp, ex) -> writeEnvelope(resp, objectMapper,
                                ErrorCode.UNAUTHORIZED, "未认证:缺少或无效的 Bearer token"))
                        .accessDeniedHandler((req, resp, ex) -> writeEnvelope(resp, objectMapper,
                                ErrorCode.FORBIDDEN, "无权限:当前角色不允许此操作")))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health", "/actuator/health/**").permitAll()
                        .requestMatchers("/api/v1/auth/token").permitAll()
                        // 内部接口的认证完全由 InternalKeyFilter 负责,不过 Spring 授权层
                        .requestMatchers("/api/v1/internal/**").permitAll()
                        .requestMatchers("/v3/api-docs/**", "/swagger-ui/**", "/swagger-ui.html").permitAll()
                        // viewer 只读:写方法统一要求 admin/analyst
                        .requestMatchers(HttpMethod.POST, "/api/v1/**").hasAnyRole("ADMIN", "ANALYST")
                        .requestMatchers(HttpMethod.PUT, "/api/v1/**").hasAnyRole("ADMIN", "ANALYST")
                        .requestMatchers(HttpMethod.PATCH, "/api/v1/**").hasAnyRole("ADMIN", "ANALYST")
                        .requestMatchers(HttpMethod.DELETE, "/api/v1/**").hasAnyRole("ADMIN", "ANALYST")
                        .anyRequest().authenticated())
                .oauth2ResourceServer(rs -> rs
                        .jwt(jwt -> jwt.jwtAuthenticationConverter(jwtAuthenticationConverter()))
                        // 坏 token 走的是 resource server 自己的入口点,也要输出统一信封
                        .authenticationEntryPoint((req, resp, ex) -> writeEnvelope(resp, objectMapper,
                                ErrorCode.UNAUTHORIZED, "未认证:缺少或无效的 Bearer token"))
                        .accessDeniedHandler((req, resp, ex) -> writeEnvelope(resp, objectMapper,
                                ErrorCode.FORBIDDEN, "无权限:当前角色不允许此操作")))
                // 不注册为 Bean,避免被 Boot 自动加进 servlet 过滤器链跑两遍
                .addFilterBefore(new InternalKeyFilter(props.internalKey(), objectMapper),
                        UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }

    @Bean
    JwtTokens jwtTokens(AppProperties props) {
        return new JwtTokens(props.jwt().secret(), props.jwt().ttl());
    }

    @Bean
    JwtDecoder jwtDecoder(JwtTokens tokens) {
        return tokens.decoder();
    }

    /** 密码哈希:默认 pbkdf2(决策 D11,600k 次迭代为 OWASP 建议),兼容校验 {bcrypt} 等前缀格式。 */
    @Bean
    PasswordEncoder passwordEncoder() {
        Map<String, PasswordEncoder> encoders = Map.of(
                // (secret, salt=16 字节, 600k 次迭代, SHA256);哈希宽度随算法为 32 字节,输出 {pbkdf2}+Base64 ≈ 72 字符
                "pbkdf2", new Pbkdf2PasswordEncoder("", 16, 600_000,
                        Pbkdf2PasswordEncoder.SecretKeyFactoryAlgorithm.PBKDF2WithHmacSHA256),
                "bcrypt", new BCryptPasswordEncoder());
        return new DelegatingPasswordEncoder("pbkdf2", encoders);
    }

    /** role 声明 → ROLE_ADMIN/ROLE_ANALYST/ROLE_VIEWER,大小写无关。 */
    private static JwtAuthenticationConverter jwtAuthenticationConverter() {
        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(jwt -> {
            String role = jwt.getClaimAsString(JwtTokens.CLAIM_ROLE);
            if (role == null || role.isBlank()) {
                return List.of();
            }
            return List.<GrantedAuthority>of(new SimpleGrantedAuthority("ROLE_" + role.toUpperCase()));
        });
        return converter;
    }

    private static void writeEnvelope(HttpServletResponse resp, ObjectMapper om, ErrorCode ec, String message)
            throws IOException {
        resp.setStatus(ec.httpStatus().value());
        resp.setContentType(MediaType.APPLICATION_JSON_VALUE);
        resp.setCharacterEncoding("UTF-8");
        om.writeValue(resp.getWriter(), ApiResponse.error(ec, message));
    }
}
