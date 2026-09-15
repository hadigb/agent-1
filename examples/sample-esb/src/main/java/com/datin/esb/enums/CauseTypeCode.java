package com.datin.esb.enums;

/** بابت تراکنش (جدول شماره 3) */
public enum CauseTypeCode {
    POSA_TRANSACTION_CAUSE_TYPE("واریز حقوق"),
    IOSP_TRANSACTION_CAUSE_TYPE("امور بیمه خدمات"),
    HIPA_TRANSACTION_CAUSE_TYPE("امور درمانی"),
    ISAP_TRANSACTION_CAUSE_TYPE("امور سرمایه گذاری و بورس"),
    FXAP_TRANSACTION_CAUSE_TYPE("امور ارزی در چهارچوب ضوابط و مقررات"),
    DRPA_TRANSACTION_CAUSE_TYPE("پرداخت قرض و تادیه دیون (قرض الحسنه، بدهی، ...)"),
    RTAP_TRANSACTION_CAUSE_TYPE("امور بازنشستگی"),
    MPTP_TRANSACTION_CAUSE_TYPE("معاملات اموال منقول"),
    IMPT_TRANSACTION_CAUSE_TYPE("معاملات اموال غیرمنقول"),
    LMAP_TRANSACTION_CAUSE_TYPE("مدیریت نقدینگی"),
    CDAP_TRANSACTION_CAUSE_TYPE("عوارض گمرکی"),
    TCAP_TRANSACTION_CAUSE_TYPE("تسویه مالیاتی"),
    GEAC_TRANSACTION_CAUSE_TYPE("سایر خدمات دولتی"),
    LRPA_TRANSACTION_CAUSE_TYPE("تسهیلات و تعهدات"),
    CCPA_TRANSACTION_CAUSE_TYPE("تودیع وثیقه"),
    GPAC_TRANSACTION_CAUSE_TYPE("هزینه عمومی و امور روزمره"),
    CPAC_TRANSACTION_CAUSE_TYPE("کمک‌های خیریه"),
    GPPC_TRANSACTION_CAUSE_TYPE("خرید کالا"),
    SPAC_TRANSACTION_CAUSE_TYPE("خرید خدمات"),
    GASP_TRANSACTION_CAUSE_TYPE("خرید کالا و خدمات"),
    FRTX_TRANSACTION_CAUSE_TYPE("سایر امور");

    private final String title;
    CauseTypeCode(String title) { this.title = title; }
}
