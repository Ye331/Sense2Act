package com.sense2act.backend.common;

import org.springframework.http.HttpStatus;

/** 统一错误码表,见 docs/api-design.md §1。code 与 HTTP 状态一一对应,语义不可私改。 */
public enum ErrorCode {
    OK(0, HttpStatus.OK),
    BAD_REQUEST(40001, HttpStatus.BAD_REQUEST),
    UNAUTHORIZED(40101, HttpStatus.UNAUTHORIZED),
    FORBIDDEN(40301, HttpStatus.FORBIDDEN),
    NOT_FOUND(40401, HttpStatus.NOT_FOUND),
    CONFLICT(40901, HttpStatus.CONFLICT),
    UNPROCESSABLE(42201, HttpStatus.UNPROCESSABLE_ENTITY),
    RATE_LIMITED(42901, HttpStatus.TOO_MANY_REQUESTS),
    SERVER_ERROR(50001, HttpStatus.INTERNAL_SERVER_ERROR);

    private final int code;
    private final HttpStatus httpStatus;

    ErrorCode(int code, HttpStatus httpStatus) {
        this.code = code;
        this.httpStatus = httpStatus;
    }

    public int code() {
        return code;
    }

    public HttpStatus httpStatus() {
        return httpStatus;
    }
}
