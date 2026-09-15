package com.datin.esb.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import java.util.List;

/** پاکت استاندارد پاسخ */
@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class ApiResponse<T> {
    /** کد وضعیت */
    private Integer rsCode;
    /** نتیجه موفق یا ناموفق */
    private Boolean isSuccess;
    /** پیغام */
    private String message;
    /** خروجی موفق - مطابق جدول شماره 1 */
    private T resultData;
    /** خروجی ناموفق - مطابق جدول شماره 2 */
    private List<ErrorItem> errorList;

    public static <T> ApiResponse<T> ok(T data) {
        ApiResponse<T> r = new ApiResponse<>();
        r.rsCode = 1; r.isSuccess = true; r.message = "عملیات با موفقیت انجام شد"; r.resultData = data;
        return r;
    }
}
