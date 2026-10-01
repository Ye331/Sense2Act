package com.sense2act.backend.common;

/** 业务异常:由全局异常处理转成统一响应包,携带错误码(HTTP 状态由错误码决定)。 */
public class BusinessException extends RuntimeException {

    private final ErrorCode errorCode;

    public BusinessException(ErrorCode errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    public static BusinessException badRequest(String message) {
        return new BusinessException(ErrorCode.BAD_REQUEST, message);
    }

    public ErrorCode errorCode() {
        return errorCode;
    }
}
