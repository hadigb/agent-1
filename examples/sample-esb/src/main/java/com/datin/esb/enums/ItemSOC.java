package com.datin.esb.enums;

/** نوع منشا وجه */
public enum ItemSOC {
    UNKNOWN_RESOURCE("نامعلوم"),
    PAPER_MONEY("اسکناس"),
    INTERNAL_DRAFT("حواله داخلی"),
    EXTERNAL_DRAFT("حواله خارجی"),
    PURCHASE_FROM_FREE_MARKET("خرید از بازار فرعی"),
    BANK_CURRENCY_RESOURCE("مبادلات موقت بانک");

    private final String title;
    ItemSOC(String title) { this.title = title; }
}
