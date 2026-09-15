package com.datin.esb.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

/** جزئیات خطا */
@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class ErrorItem {
    private Integer code;      // کد خطا
    private String desc;       // پیغام خطا
    private String paramName;  // نام پارامتر دارای خطا
    private String paramPath;  // مسیر پارامتر دارای خطا

    public ErrorItem() {}
    public ErrorItem(Integer code, String desc, String paramName, String paramPath) {
        this.code = code; this.desc = desc; this.paramName = paramName; this.paramPath = paramPath;
    }
}
