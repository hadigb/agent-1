package com.datin.esb.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;

public class BillStructureRequest {
    /** شماره سپرده */
    @NotBlank
    @JsonProperty("DepositNumber")
    private String depositNumber;

    public String getDepositNumber() { return depositNumber; }
}
