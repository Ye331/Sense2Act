package com.sense2act.backend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.ErrorCode;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Set;

/**
 * 内部接口门卫:/api/v1/internal/* 只认 X-Internal-Key。
 * 未配置密钥 → 整组 403(40301);配置了但不匹配 → 401(40101)。见 api-design.md §9。
 *
 * 另外,数据查询接口(documents/organizations,api-design §1)的读请求也接受内部 key:
 * 外部服务(如 Agent 调查服务,§9.3)经 X-Internal-Key 读数据,无需 JWT。
 * 认证身份只带 ROLE_INTERNAL、不带用户角色 → 写操作照旧被方法安全层拦下(40301)。
 */
public class InternalKeyFilter extends OncePerRequestFilter {

    public static final String INTERNAL_PREFIX = "/api/v1/internal";
    public static final String HEADER = "X-Internal-Key";

    /** 数据查询接口:GET/HEAD 可用内部 key 认证(其余方法仍走 JWT 与角色层)。 */
    private static final Set<String> DATA_READ_PREFIXES = Set.of(
            "/api/v1/documents", "/api/v1/organizations");

    private final String internalKey;
    private final ObjectMapper objectMapper;

    public InternalKeyFilter(String internalKey, ObjectMapper objectMapper) {
        this.internalKey = internalKey;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith(INTERNAL_PREFIX) && !dataReadPath(request);
    }

    private boolean dataReadPath(HttpServletRequest request) {
        String method = request.getMethod();
        if (!"GET".equals(method) && !"HEAD".equals(method)) {
            return false;
        }
        String uri = request.getRequestURI();
        return DATA_READ_PREFIXES.stream().anyMatch(p -> uri.equals(p) || uri.startsWith(p + "/"));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (request.getRequestURI().startsWith(INTERNAL_PREFIX)) {
            // 内部接口:必须带对 key 才放行
            if (internalKey == null || internalKey.isBlank()) {
                write(response, ErrorCode.FORBIDDEN, "INTERNAL_API_KEY 未配置,内部接口整组禁用");
                return;
            }
            if (keyMismatch(request)) {
                write(response, ErrorCode.UNAUTHORIZED, "X-Internal-Key 缺失或不正确");
                return;
            }
            chain.doFilter(request, response);
            return;
        }
        // 数据查询读接口:带 key 就验 key(错 key 直接 40101,不静默落到 JWT),不带 key 走 JWT
        if (request.getHeader(HEADER) != null) {
            if (internalKey == null || internalKey.isBlank() || keyMismatch(request)) {
                write(response, ErrorCode.UNAUTHORIZED, "X-Internal-Key 缺失或不正确");
                return;
            }
            SecurityContextHolder.getContext().setAuthentication(new InternalAuthentication(internalKey));
        }
        chain.doFilter(request, response);
    }

    private boolean keyMismatch(HttpServletRequest request) {
        String provided = request.getHeader(HEADER);
        return provided == null || provided.isBlank() || !internalKey.equals(provided);
    }

    private void write(HttpServletResponse response, ErrorCode ec, String message) throws IOException {
        response.setStatus(ec.httpStatus().value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        objectMapper.writeValue(response.getWriter(), ApiResponse.error(ec, message));
    }

    /** 内部服务身份:只有 ROLE_INTERNAL,够过 authenticated() 读数据,过不了任何用户角色检查。 */
    private static final class InternalAuthentication extends AbstractAuthenticationToken {

        private InternalAuthentication(String key) {
            super(AuthorityUtils.createAuthorityList("ROLE_INTERNAL"));
            setDetails(key);
            setAuthenticated(true);
        }

        @Override
        public Object getCredentials() {
            return null;
        }

        @Override
        public Object getPrincipal() {
            return "internal-service";
        }
    }
}
