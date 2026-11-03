<#
.SYNOPSIS
Exports the effective (direct + nested) membership of the ACE Reserves AD groups to a CSV snapshot.

.NOTES
Read-only: this script never modifies Active Directory.
#>
[CmdletBinding()]
param(
    [string[]]$GroupName = @(
        'az-ACE-Reserves-Algeria-prod',
        'az-ACE-Reserves-Bolivia-prod',
        'az-ACE-Reserves-Dolphin-prod',
        'az-ACE-Reserves-UAE-prod',
        'az-ACE-Reserves-GOM-prod',
        'az-ACE-Reserves-Oman-prod',
        'az-ACE-Reserves-PermianEOR-prod',
        'az-ACE-Reserves-PermianResources-prod',
        'az-ACE-Reserves-Rockies-prod',
        'az-ACE-Reserves-Corporate-prod',
        'az-ACE-Reserves-DolphinandUAE-prod',
        'az-ACE-Reserves-IntlGOM-prod',
        'az-ACE-Reserves-ORCM-prod'
    ),

    [string]$OutputPath = (Join-Path $PSScriptRoot '..\..\data\members.csv')
)

$ErrorActionPreference = 'Stop'
Import-Module ActiveDirectory

$memberProperties = 'objectGUID', 'sAMAccountName', 'displayName', 'mail', 'title', 'department', 'manager',
    'lastLogonTimestamp', 'userAccountControl'
$accountDisabledFlag = 2
$memberCache = @{}
$managerNameCache = @{}
$rows = [System.Collections.Generic.List[object]]::new()

function Get-ManagerName {
    param([string]$ManagerDn)

    if (-not $ManagerDn) {
        return ''
    }

    if (-not $managerNameCache.ContainsKey($ManagerDn)) {
        try {
            $managerNameCache[$ManagerDn] = [string](Get-ADUser -Identity $ManagerDn -Properties DisplayName).DisplayName
        }
        catch {
            $managerNameCache[$ManagerDn] = ''
        }
    }

    return $managerNameCache[$ManagerDn]
}

function Get-MemberDetail {
    param([Parameter(Mandatory)][string]$MemberDn)

    if ($memberCache.ContainsKey($MemberDn)) {
        return $memberCache[$MemberDn]
    }

    try {
        $adObject = Get-ADObject -Identity $MemberDn -Properties $memberProperties
        $isUser = $adObject.ObjectClass -eq 'user'
        $detail = [pscustomobject]@{
            MemberObjectGuid = $adObject.ObjectGUID.ToString()
            ObjectClass      = [string]$adObject.ObjectClass
            SamAccountName   = [string]$adObject.sAMAccountName
            DisplayName      = if ($adObject.displayName) { [string]$adObject.displayName } else { [string]$adObject.Name }
            Mail             = [string]$adObject.mail
            Title            = [string]$adObject.title
            Department       = [string]$adObject.department
            ManagerName      = Get-ManagerName -ManagerDn $adObject.manager
            Enabled          = if ($isUser) { [string](-not ($adObject.userAccountControl -band $accountDisabledFlag)) } else { '' }
            LastLogonDate    = if ($adObject.lastLogonTimestamp) { [DateTime]::FromFileTime($adObject.lastLogonTimestamp).ToString('yyyy-MM-dd') } else { '' }
        }
    }
    catch {
        Write-Warning ('Could not resolve member {0}: {1}' -f $MemberDn, $_.Exception.Message)
        $detail = [pscustomobject]@{
            MemberObjectGuid = $MemberDn
            ObjectClass      = 'unresolved'
            SamAccountName   = ''
            DisplayName      = $MemberDn
            Mail             = ''
            Title            = ''
            Department       = ''
            ManagerName      = ''
            Enabled          = ''
            LastLogonDate    = ''
        }
    }

    $memberCache[$MemberDn] = $detail
    return $detail
}

function Add-MemberRows {
    param(
        [Parameter(Mandatory)][string]$TargetGroupName,
        [Parameter(Mandatory)][string]$GroupDn,
        [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.Generic.HashSet[string]]$VisitedGroupDns
    )

    if (-not $VisitedGroupDns.Add($GroupDn)) {
        return
    }

    $group = Get-ADGroup -Identity $GroupDn -Properties member

    foreach ($memberDn in $group.member) {
        $detail = Get-MemberDetail -MemberDn $memberDn
        $row = $detail | Select-Object *
        $row | Add-Member -NotePropertyName GroupName -NotePropertyValue $TargetGroupName
        $row | Add-Member -NotePropertyName ViaGroup -NotePropertyValue $group.Name
        $rows.Add($row)

        if ($detail.ObjectClass -eq 'group') {
            Add-MemberRows -TargetGroupName $TargetGroupName -GroupDn $memberDn -VisitedGroupDns $VisitedGroupDns
        }
    }
}

foreach ($name in $GroupName) {
    try {
        $topGroup = Get-ADGroup -Identity $name
    }
    catch {
        Write-Warning ('Group not found: {0}' -f $name)
        continue
    }

    $countBefore = $rows.Count
    $visitedGroupDns = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    Add-MemberRows -TargetGroupName $topGroup.Name -GroupDn $topGroup.DistinguishedName -VisitedGroupDns $visitedGroupDns
    Write-Host ('{0}: {1} membership path(s)' -f $topGroup.Name, ($rows.Count - $countBefore))
}

$outputDirectory = Split-Path -Path $OutputPath -Parent
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$snapshotUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

$rows |
    Group-Object -Property GroupName, MemberObjectGuid |
    ForEach-Object {
        $first = $_.Group[0]
        $isDirect = [bool]($_.Group | Where-Object { $_.ViaGroup -eq $first.GroupName })
        $viaGroups = @(
            $_.Group |
                Where-Object { $_.ViaGroup -ne $first.GroupName } |
                Select-Object -ExpandProperty ViaGroup -Unique
        )

        if ($isDirect -and $viaGroups.Count -gt 0) {
            $membership = 'Direct + Nested'
        }
        elseif ($isDirect) {
            $membership = 'Direct'
        }
        else {
            $membership = 'Nested'
        }

        [pscustomobject]@{
            GroupName        = $first.GroupName
            MemberObjectGuid = $first.MemberObjectGuid
            ObjectClass      = $first.ObjectClass
            SamAccountName   = $first.SamAccountName
            DisplayName      = $first.DisplayName
            Mail             = $first.Mail
            Title            = $first.Title
            Department       = $first.Department
            ManagerName      = $first.ManagerName
            Enabled          = $first.Enabled
            LastLogonDate    = $first.LastLogonDate
            Membership       = $membership
            ViaGroups        = $viaGroups -join '; '
            SnapshotUtc      = $snapshotUtc
        }
    } |
    Sort-Object -Property GroupName, DisplayName |
    Export-Csv -Path $OutputPath -NoTypeInformation -Encoding utf8

Write-Host ('Snapshot written to {0}' -f (Resolve-Path $OutputPath))
