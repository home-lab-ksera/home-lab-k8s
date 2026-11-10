<?xml version="1.0"?>
<!-- libvirt domain XML の補正。provider が属性を持たない設定だけをここで追加する。 -->
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:output omit-xml-declaration="yes" indent="yes"/>

  <xsl:template match="node()|@*">
    <xsl:copy>
      <xsl:apply-templates select="node()|@*"/>
    </xsl:copy>
  </xsl:template>

  <!--
    ゲストで解放されたページをホストへ返し、qemu RSS の高止まりとホスト swap を防ぐ。
    provider の生成 XML に memballoon が無い場合は libvirt が既定値で補うため、ここで明示的に追加する。
  -->
  <xsl:template match="/domain/devices">
    <xsl:copy>
      <xsl:apply-templates select="node()|@*"/>
      <xsl:if test="not(memballoon)">
        <memballoon model="virtio" freePageReporting="on"/>
      </xsl:if>
    </xsl:copy>
  </xsl:template>

  <xsl:template match="/domain/devices/memballoon[@model='virtio']">
    <xsl:copy>
      <xsl:apply-templates select="@*"/>
      <xsl:attribute name="freePageReporting">on</xsl:attribute>
      <xsl:apply-templates select="node()"/>
    </xsl:copy>
  </xsl:template>
</xsl:stylesheet>
