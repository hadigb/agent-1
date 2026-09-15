package com.datin.esb.enums;

/** ابزار برداشت (وسیله برداشت به معنای روش‌های مجاز جهت برداشت از سپرده می‌باشد) */
public enum WithdrawalTool {
    chequeTool("دسته چک"),
    orderedPaymentTool("دستور پرداخت"),
    cardTool("کارت"),
    internalDocumentTool("سند داخلی"),
    internetTool("اینترنتی"),
    telephoneTool("تلفنی");

    private final String title;
    WithdrawalTool(String title) { this.title = title; }
    public String getTitle() { return title; }
}
