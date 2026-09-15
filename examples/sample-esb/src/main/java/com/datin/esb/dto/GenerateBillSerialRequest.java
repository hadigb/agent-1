package com.datin.esb.dto;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;

@JsonNaming(PropertyNamingStrategies.UpperCamelCaseStrategy.class)
public class GenerateBillSerialRequest {
    /** شماره سپرده */
    @NotBlank
    private String depositNumber;
    /** بستانکار/بدهکار (در حالت دیفالت false است) 0: بستانکار 1: بدهکار */
    private Boolean isDebtor;
    /** مبلغ */
    @NotNull
    private BigDecimal amount;
    /** کلید (شناسه قبض) */
    @NotBlank
    private String key;
    /** مقدار شناسه قبض */
    @NotBlank
    private String value;
}
