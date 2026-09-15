package com.example.stress;

import java.util.*;
import java.util.function.*;
import static java.util.stream.Collectors.toList;

@SuppressWarnings({"unchecked", "rawtypes"})
@Deprecated(since = "1.2", forRemoval = true)
public final class Stress<K extends Comparable<? super K>, V> implements Map.Entry<K, V>, Comparable<Stress<K, V>> {

    @interface Meta { Class<?>[] types() default {}; Nested nested() default @Nested(value = 3, names = {"a", "b"}); }
    @interface Nested { int value(); String[] names(); }

    private static final Map<String, List<? extends Number>> CACHE = new HashMap<>() {{ put("x", List.of(1, 2)); }};
    private final Comparator<K> cmp = (a, b) -> a.compareTo(b) < 0 ? -1 : (a.compareTo(b) > 0 ? 1 : 0);
    private int[][] grid = new int[3][4], other = {{1}, {2, 3}};
    private char open = '{', close = '}', quote = '"', bs = '\\';
    private String tricky = "}{ // not a comment /* nor this */ \"quoted\" A";
    private String block = """
        line with "quotes" and /* comment-like */ text
        and a closing brace }
        """;
    private Supplier<Runnable> sup = () -> () -> { synchronized (this) { System.out.println("{" + "}"); } };
    private final Object anon = new Object() { @Override public String toString() { return "anon"; } };
    boolean flag = 1 < 2 && 3 > 2, other2 = CACHE.size() < 3;
    List<Map.Entry<String, Integer>> entries = new ArrayList<Map.Entry<String, Integer>>();

    static { System.out.println("static init"); }
    { System.out.println("instance init"); }

    public Stress() { this(null, null); }
    private Stress(K k, V v) { super(); }

    @Meta(types = {String.class, Integer.class})
    public <T extends Number & Comparable<T>> T pick(List<? super T> in, T... rest) throws IllegalStateException {
        label:
        for (var x : rest) {
            if (x instanceof Integer i && i > 3) continue label;
            switch (x.intValue()) {
                case 1 -> { yield_(); }
                case 2, 3 -> System.out.println("two or three");
                default -> throw new IllegalStateException(String.format("bad %d", x));
            }
            int r = switch (x.intValue()) { case 1: yield 10; default: { int q = 2; yield q * 5; } };
            record Local(int a, String b) {}
            class LocalClass { int z; }
            try (var sc = new Scanner(System.in); Scanner sc2 = new Scanner("")) {
                sc.nextLine();
            } catch (IllegalStateException | NoSuchElementException e) {
                throw e;
            } finally {
                System.out.println("done");
            }
        }
        Function<Integer, Integer> f = Stress::<Integer>identity;
        Object o = (Comparable<String> & java.io.Serializable) "x";
        int[] arr = new int[]{1, 2};
        boolean b = arr.length < 3 && rest.length > 0;
        List<String> names = Arrays.stream(new String[]{"a"}).map(String::toUpperCase).collect(toList());
        return rest.length > 0 ? rest[0] : null;
    }

    private void yield_() {}

    static <T> T identity(T t) { return t; }

    @Override public K getKey() { return null; }
    @Override public V getValue() { return null; }
    @Override public V setValue(V value) { return value; }
    @Override public int compareTo(Stress<K, V> o) { return 0; }

    public sealed interface Shape permits Circle, Square {}
    public record Circle(double radius) implements Shape { public Circle { if (radius < 0) throw new IllegalArgumentException(); } }
    public non-sealed static class Square implements Shape { double side; }

    public enum Op implements IntBinaryOperator {
        PLUS("+") { @Override public int applyAsInt(int a, int b) { return a + b; } },
        MINUS("-") { @Override public int applyAsInt(int a, int b) { return a - b; } };
        private final String symbol;
        Op(String symbol) { this.symbol = symbol; }
        public abstract int applyAsInt(int a, int b);
    }

    interface Visitor<R> { R visit(Stress<?, ?> s); static <R> Visitor<R> noop() { return s -> null; } private void helper() {} }

    public static void main(String... args) throws Exception {
        Stress<String, Integer> s = new Stress<>();
        var list = new ArrayList<Map<String, List<Integer>>>();
        Map<String, Integer> m = new TreeMap<>(Comparator.reverseOrder());
        m.forEach((k, v) -> { if (k.length() > v) return; });
        new Thread(() -> {}).start();
        int x = 0b1010_1010 + 0x1F + 07 + 1_000_000 + (int) 1.5e3f + 'c';
        long big = 9_223_372_036_854_775_807L;
        double d = .5 + 1. + 1e-3;
        String s2 = "a" + 1 + 'b' + 2.0 + true + null;
        System.out.printf("%s %d%n", s2, x);
        if (s instanceof Stress<?, ?> st) System.out.println(st);
        Runnable r = Stress.class.getName().isEmpty() ? null : () -> {};
    }
}

class Outer { class Inner { class Deep { int d; } } static class SN { } }
interface Marker {}
enum Empty {}
@interface Ann { }
