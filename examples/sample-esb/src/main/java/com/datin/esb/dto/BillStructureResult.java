package com.datin.esb.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class BillStructureResult {
    /** نوع مشخصه عملیات مالی در اطلاعات قبض عملیات مالی */
    @JsonProperty("TransferMoneyBillStructure")
    private String transferMoneyBillStructure;
}
