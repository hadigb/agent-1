package com.datin.esb.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.util.List;

/** پاسخ پایه همه سرویس‌ها */
@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class BaseResponse {
    /** نتیجه موفق یا ناموفق */
    private Boolean isSuccess;
    /** پیغام */
    private String message;
    /** کد استاندارد طبق جدول پیوست */
    private Integer rsCode;
    /** خروجی ناموفق - لیست خطاها */
    private List<ErrorItem> errorList;

    public Boolean getIsSuccess() { return isSuccess; }
    public void setIsSuccess(Boolean isSuccess) { this.isSuccess = isSuccess; }
    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }
    public Integer getRsCode() { return rsCode; }
    public void setRsCode(Integer rsCode) { this.rsCode = rsCode; }
    public List<ErrorItem> getErrorList() { return errorList; }
    public void setErrorList(List<ErrorItem> errorList) { this.errorList = errorList; }
}
