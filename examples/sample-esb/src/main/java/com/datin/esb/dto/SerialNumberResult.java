package com.datin.esb.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class SerialNumberResult {
    /** شماره سریال قبض */
    @JsonProperty("SerialNumber")
    private String serialNumber;
}
