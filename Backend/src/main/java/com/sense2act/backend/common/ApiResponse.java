package com.sense2act.backend.common;

/** 统一响应包 {code, message, data},见 docs/api-design.md §1。 */
public record ApiResponse<T>(int code, String message, T data) {

    public static <T> ApiResponse<T> ok(T data) {
        return new ApiResponse<>(ErrorCode.OK.code(), "ok", data);
    }

    public static ApiResponse<Void> ok() {
        return new ApiResponse<>(ErrorCode.OK.code(), "ok", null);
    }

    public static <T> ApiResponse<T> error(ErrorCode ec, String message) {
        return new ApiResponse<>(ec.code(), message, null);
    }
}
