package com.datin.esb.dto;

import com.datin.esb.enums.CauseTypeCode;
import com.datin.esb.enums.TransactionChannel;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import java.util.List;

/**
 * درخواست ثبت سند حسابداری.
 */
@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class IssueDocumentRequest {

    /** شناسه یکتا تراکنش */
    @NotBlank
    private String transactionId;

    /** نمایش شماره سند؛ مقدار پیش‌فرض false است. در صورت true بودن، فیلد DocumentNumber در خروجی نمایش داده می‌شود. */
    private Boolean showDocumentNumber = Boolean.FALSE;

    // شناسه واریز (رجوع شود به نکات)
    private String transferBillNumber;

    @NotBlank
    private String documentTitle; // عنوان سند

    private String terminalCode; // کد ترمینال

    /** کانال تراکنش */
    private TransactionChannel transactionChannel;

    /** آیا درخواست از CMS ارسال می‌شود؟ */
    private Boolean fromCMS;

    private String documentTemplateCode;

    /** عدم کنترل اطلاعات سیاح */
    private String documentExtraInfoIgnoreSayahBizCheck;

    /** نوع تراکنش پاد */
    private String transactionType;

    /** کد صنف */
    private String businessCode;

    /**
     * حساب پادی هست یا خیر. این فیلد اختیاری است؛ چنانچه سپرده/شبای مبدا سنتی باشد مقدار false
     * و چنانچه سپرده/شبای مبدا دیجیتال باشد مقدار true باید ارسال شود.
     */
    private Boolean isPod;

    /** بابت (طبق جدول شماره 3) */
    private CauseTypeCode causeTypeCode;

    /** درگاه تراکنش. مقدار قابل قبول برای این فیلد: CashingCheque (وصول چک) */
    private String issueType;

    /** کد صنفی کسب و کار */
    private String guildCode;

    /** شناسه کیف پول */
    private String wallet;

    /** بندهای سند */
    @NotEmpty
    @Valid
    private List<DocumentItem> documentItem;

    public String getTransactionId() { return transactionId; }
    public void setTransactionId(String transactionId) { this.transactionId = transactionId; }
    public Boolean getShowDocumentNumber() { return showDocumentNumber; }
    public String getDocumentTitle() { return documentTitle; }
    public List<DocumentItem> getDocumentItem() { return documentItem; }
    public Boolean getIsPod() { return isPod; }
    public String getGuildCode() { return guildCode; }
    public String getWallet() { return wallet; }
    public TransactionChannel getTransactionChannel() { return transactionChannel; }
}
