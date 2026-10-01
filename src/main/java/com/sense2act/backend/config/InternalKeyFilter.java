package com.sense2act.backend.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.ErrorCode;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

/**
 * 内部接口门卫:/api/v1/internal/* 只认 X-Internal-Key。
 * 未配置密钥 → 整组 403(40301);配置了但不匹配 → 401(40101)。见 api-design.md §9。
 */
public class InternalKeyFilter extends OncePerRequestFilter {

    public static final String INTERNAL_PREFIX = "/api/v1/internal";
    public static final String HEADER = "X-Internal-Key";

    private final String internalKey;
    private final ObjectMapper objectMapper;

    public InternalKeyFilter(String internalKey, ObjectMapper objectMapper) {
        this.internalKey = internalKey;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !request.getRequestURI().startsWith(INTERNAL_PREFIX);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (internalKey == null || internalKey.isBlank()) {
            write(response, ErrorCode.FORBIDDEN, "INTERNAL_API_KEY 未配置,内部接口整组禁用");
            return;
        }
        String provided = request.getHeader(HEADER);
        if (provided == null || provided.isBlank() || !internalKey.equals(provided)) {
            write(response, ErrorCode.UNAUTHORIZED, "X-Internal-Key 缺失或不正确");
            return;
        }
        chain.doFilter(request, response);
    }

    private void write(HttpServletResponse response, ErrorCode ec, String message) throws IOException {
        response.setStatus(ec.httpStatus().value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        objectMapper.writeValue(response.getWriter(), ApiResponse.error(ec, message));
    }
}
