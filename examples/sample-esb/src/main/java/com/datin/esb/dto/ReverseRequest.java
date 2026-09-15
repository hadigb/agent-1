package com.datin.esb.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import jakarta.validation.constraints.NotBlank;

/** درخواست برگشت تراکنش */
@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class ReverseRequest {
    /** شناسه یکتای تراکنش */
    @NotBlank
    private String transactionId;
    /**
     * false: عملیات ریورس به صورت همزمان انجام شده و پاسخ عملیات در خروجی بازگردانی می‌شود.
     * true: عملیات ریورس به صورت غیرهمزمان انجام خواهد شد و نتیجه سرویس به صورت موفق بازگردانده می‌شود.
     */
    private Boolean isAsync = Boolean.TRUE;

    public String getTransactionId() { return transactionId; }
    public Boolean getIsAsync() { return isAsync; }
}
