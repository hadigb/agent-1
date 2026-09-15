package com.datin.esb.dto;

import com.datin.esb.enums.DocumentItemType;
import com.datin.esb.enums.ItemSOC;
import com.datin.esb.enums.WithdrawalTool;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.util.List;

/** بند سند */
@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class DocumentItem {

    /** بستانکار: False / بدهکار: True */
    @NotNull
    private Boolean isDebtor;

    /** کد کاربری که حساب مجازی صندوق او در ارز ریال مد نظر می‌باشد (در صورتی که DocumentItemType برابر VIRTUALBOX باشد اجباری است) */
    private String personnelCode;

    /** شماره حساب / سپرده / سرفصل. در صورتی که DocumentItemType مخالف VIRTUALBOX و BOX باشد این فیلد اجباری است */
    @NotBlank
    private String accountNumber;

    @NotNull
    private DocumentItemType documentItemType;

    /** در صورتی که DocumentItemType از نوع TOPIC یا VIRTUALBOX باشد، اجباری می‌باشد */
    private String branchCode;

    /** مبلغ. این فیلد مقدار اعشاری را نیز ساپورت می‌کند */
    @NotNull
    private Long amount;

    /** ابزار برداشت */
    private WithdrawalTool withdrawalTool;

    /** شرح بند */
    @NotBlank
    private String comment;

    /** شناسه قبض ثانویه */
    private String secondaryBillSerialNumber;

    /** نوع منشا وجه. این فیلد اختیاری شرطی است: در صورت ارسال مقدار برای IsoCode ارسال این فیلد حتما لازم است */
    private ItemSOC itemSOC;

    /** ارز مورد نظر */
    private String isoCode;

    /** شناسه قبض سپرده؛ در صورتی که سپرده وارد شده قبضی باشد ارسال آن الزامی است */
    private String billNumber;

    /** لیست مشتریان سپرده (اختیاری - به بخش نکات شرایط برداشت مراجعه شود) */
    private List<String> customerNumbers;

    /** داده‌های کاربر */
    private List<MetaData> itemUserMetaData;

    /** داده‌های سیستم؛ مقادیر مجاز key مطابق با فایل اکسل دریافتی */
    private List<MetaData> systemMetaData;

    public Boolean getIsDebtor() { return isDebtor; }
    public String getAccountNumber() { return accountNumber; }
    public DocumentItemType getDocumentItemType() { return documentItemType; }
    public Long getAmount() { return amount; }
    public String getIsoCode() { return isoCode; }
    public ItemSOC getItemSOC() { return itemSOC; }
    public List<String> getCustomerNumbers() { return customerNumbers; }
}
