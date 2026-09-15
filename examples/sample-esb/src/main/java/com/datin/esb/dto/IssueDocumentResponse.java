package com.datin.esb.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

/** پاسخ سرویس ثبت سند */
@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class IssueDocumentResponse extends BaseResponse {
    /** شناسه یکتا تراکنش */
    private String transactionId;
    /** تاریخ انجام تراکنش بصورت شمسی و با فرمت yyyy/mm/dd - hh:mm:ss */
    private String transactiondate;
    /** کد تراکنش. در صورتی که حساب مبدا دیجیتال باشد این فیلد null خواهد بود */
    private String transactionCode;
    /** شماره سند - اگر فیلد ShowDocumentNumber برابر با false باشد این فیلد در خروجی نمایش داده نمی‌شود */
    private String documentNumber;

    public String getTransactionId() { return transactionId; }
    public void setTransactionId(String transactionId) { this.transactionId = transactionId; }
    public void setTransactiondate(String d) { this.transactiondate = d; }
    public void setTransactionCode(String c) { this.transactionCode = c; }
    public void setDocumentNumber(String n) { this.documentNumber = n; }
}
