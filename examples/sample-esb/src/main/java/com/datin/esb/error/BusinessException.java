package com.datin.esb.error;

public class BusinessException extends RuntimeException {
    private final ErrorCode errorCode;
    private final String paramName;

    public BusinessException(ErrorCode errorCode) { this(errorCode, null); }

    public BusinessException(ErrorCode errorCode, String paramName) {
        super(errorCode.getMessage());
        this.errorCode = errorCode;
        this.paramName = paramName;
    }

    public ErrorCode getErrorCode() { return errorCode; }
}
