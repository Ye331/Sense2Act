package com.sense2act.backend.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * SSE 流接口的 JWT 查询参数支持(契约 §7):浏览器 EventSource 设不了 Authorization 头,
 * 允许 ?token=<jwt> 承载。本过滤器把它折叠成标准头,后续认证链无感知;
 * 仅对 GET 的流路径生效,不放宽其他接口。
 */
public class SseTokenParamFilter extends OncePerRequestFilter {

    private static final String TOKEN_PARAM = "token";

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        if (!"GET".equals(request.getMethod())) {
            return true;
        }
        String uri = request.getRequestURI();
        return !(uri.endsWith("/stream") && uri.startsWith("/api/v1/"));
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String token = request.getParameter(TOKEN_PARAM);
        boolean hasHeader = request.getHeader("Authorization") != null;
        if (token == null || token.isBlank() || hasHeader) {
            chain.doFilter(request, response);
            return;
        }
        chain.doFilter(new AuthHeaderInjectingRequest(request, "Bearer " + token), response);
    }

    private static final class AuthHeaderInjectingRequest extends HttpServletRequestWrapper {

        private static final String NAME = "Authorization";

        private final String value;

        private AuthHeaderInjectingRequest(HttpServletRequest request, String value) {
            super(request);
            this.value = value;
        }

        @Override
        public String getHeader(String name) {
            return NAME.equalsIgnoreCase(name) ? value : super.getHeader(name);
        }

        @Override
        public Enumeration<String> getHeaders(String name) {
            return NAME.equalsIgnoreCase(name) ? Collections.enumeration(List.of(value)) : super.getHeaders(name);
        }

        @Override
        public Enumeration<String> getHeaderNames() {
            Set<String> names = new LinkedHashSet<>(Collections.list(super.getHeaderNames()));
            names.add(NAME);
            return Collections.enumeration(names);
        }
    }
}
