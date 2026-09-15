package com.datin.esb.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/** زوج کلید/مقدار داده‌های اکسترا اینفو */
public class MetaData {
    @JsonProperty("Key")
    private String key;   // فیلد
    @JsonProperty("Value")
    private String value; // مقدار فیلد

    public String getKey() { return key; }
    public String getValue() { return value; }
}
